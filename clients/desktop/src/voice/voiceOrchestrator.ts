import { cancelJvsSession, streamSynthesizeByJvs, synthesizeByJvs, warmJvsAudioModels } from "./voiceBridge";
import { splitSentences } from "./sentenceBuffer";
import { prepareSentenceForTtsDetailed } from "./speakableText";
import { voicePlaybackController } from "./voicePlaybackController";
import { voiceSessionStore } from "./voiceSessionStore";
import { truncVoiceLog, voiceCompanionDebug } from "./voiceCompanionDebugLog";
import { voiceChatTraceIfActive } from "./voiceChatTraceLog";
import { notifyCompanionVoicePhase } from "./voiceNativeBridge";
import { synthesizeSpeechL2Only } from "../lib/api";
import { DEFAULT_KOKORO_TTS_VOICE } from "./voiceDefaults";
import { stripAssistantUiProtocol } from "../components/Chat/pendingConfirmationProtocol";

type ChunkConsumer = (text: string) => void;

type SpeechJobKind = "content" | "cue";

type SpeechJobContext = {
  generation: number;
  sessionId: string;
  ttsVoice?: string;
  companionUi: boolean;
  queuedAt: number;
  kind: SpeechJobKind;
  reason?: string;
  sentenceUnits: number;
};

type PendingContentSegment = {
  text: string;
  onSentence?: ChunkConsumer;
  units: number;
};

const CONTENT_COALESCE_MAX_CHARS = 90;
const CONTENT_COALESCE_DELAY_MS = 560;
const TTS_FIRST_AUDIO_SLOW_MS = 1500;
const FAST_TTS_MAX_NORMALIZED_CHARS = 12;
const CLOUD_LEAD_MIN_CHARS = 8;
const CLOUD_LEAD_MAX_CHARS = 15;
const ENABLE_CLOUD_FAST_LEAD_SPLIT = false;
const SOFT_BOUNDARY_RE = /[\uFF0C,\u3001\uFF1B;\uFF1A:]$/;
const HARD_BOUNDARY_RE = /[\u3002\uFF01\uFF1F.!?]$/;
const PROTECTED_TTS_TERMS = ["Lark", "Vivian", "Jachin", "Codex", "Ethan", "Qwen", "DashScope", "CosyVoice"];

export type VoiceOrchestratorSessionOpts = {
  /** 0 = 不朗读 */
  maxSpeakSentences?: number;
  /** 是否更新 Orb / Rust companion phase（大窗 PTT 应为 false） */
  companionUi?: boolean;
  /** JVS 音色 ID（如 zf_001）。未传则走服务端默认音色。 */
  ttsVoice?: string;
};

/**
 * Phase A skeleton:
 * - Manage voice session states.
 * - Buffer L3 chunks to sentence-level units.
 * - Dispatch sentence TTS requests to JVS and queue audio playback.
 */
export class VoiceOrchestrator {
  private sentenceRemainder = "";
  private sessionId = "";
  private generation = 0;
  private chunkChain: Promise<void> = Promise.resolve();
  private ttsChain: Promise<void> = Promise.resolve();
  private lastChunkIn = "";
  private spokenSentenceCount = 0;
  private spokenOrQueuedSentenceKeys = new Set<string>();
  private maxSpeakSentences = 3;
  private companionUi = true;
  private ttsVoice?: string;
  private pendingContentSegments: PendingContentSegment[] = [];
  private pendingContentFlushTimer: ReturnType<typeof setTimeout> | null = null;
  private sessionStartedAt = 0;
  private firstL3ChunkLogged = false;
  private firstTtsTextLogged = false;

  /**
   * 仅把“完整句”计入 maxSpeakSentences。
   * 逗号/顿号级分段用于加速首播，不应被当作一句而过早触发上限。
   */
  private isHardSentenceBoundary(text: string): boolean {
    const t = text.trim();
    if (!t) return false;
    return HARD_BOUNDARY_RE.test(t);
  }

  private isSoftSentenceBoundary(text: string): boolean {
    const t = text.trim();
    if (!t) return false;
    return SOFT_BOUNDARY_RE.test(t);
  }

  private countHardSentenceUnits(text: string): number {
    const count = (text.match(/[\u3002\uff01\uff1f.!?]/g) ?? []).length;
    return Math.max(1, count);
  }

  private preferMandarinNeuralVoice(): boolean {
    const v = (this.ttsVoice || "").trim();
    if (!v) return false;
    return /^zh-CN-.*Neural$/i.test(v);
  }

  private sentenceDedupeKey(text: string): string {
    return text
      .replace(/[\s\u3000]+/g, "")
      .replace(/[。！？.!?，,、；;：:"'“”’（）()\[\]【】]+$/g, "")
      .trim();
  }

  private ttsRequestKindFor(speakable: string, job: SpeechJobContext): SpeechJobKind {
    if (job.kind === "cue") return "cue";
    return "content";
  }

  private isProtectedSplitIndex(text: string, index: number): boolean {
    if (index <= 0 || index >= text.length) return true;
    const before = text[index - 1] || "";
    const after = text[index] || "";
    if (/[A-Za-z0-9_]/.test(before) && /[A-Za-z0-9_]/.test(after)) return true;
    const lower = text.toLowerCase();
    for (const term of PROTECTED_TTS_TERMS) {
      const start = lower.indexOf(term.toLowerCase());
      if (start >= 0 && index > start && index < start + term.length) return true;
    }
    return false;
  }

  private findLeadSplitIndex(text: string): number {
    const t = text.trim();
    const min = Math.min(CLOUD_LEAD_MIN_CHARS, Math.max(0, t.length - 1));
    const max = Math.min(CLOUD_LEAD_MAX_CHARS, t.length - 1);
    for (let i = min; i <= max; i += 1) {
      const left = t.slice(0, i + 1).trim();
      if (SOFT_BOUNDARY_RE.test(left) && !this.isProtectedSplitIndex(t, i + 1)) return i + 1;
    }
    return -1;
  }

  private splitCloudLead(text: string): [string, string] | null {
    if (!ENABLE_CLOUD_FAST_LEAD_SPLIT) return null;
    const t = text.trim();
    if (t.length <= CLOUD_LEAD_MAX_CHARS + 6) return null;
    const splitAt = this.findLeadSplitIndex(t);
    if (splitAt <= 0) return null;
    const lead = t.slice(0, splitAt).trim();
    const rest = t.slice(splitAt).trim();
    if (!lead || !rest) return null;
    return [lead, rest];
  }

  startSession(sessionId: string, opts?: VoiceOrchestratorSessionOpts): void {
    this.sessionId = sessionId;
    this.sentenceRemainder = "";
    this.lastChunkIn = "";
    this.spokenSentenceCount = 0;
    this.spokenOrQueuedSentenceKeys.clear();
    this.clearPendingContentFlushTimer();
    this.pendingContentSegments = [];
    this.sessionStartedAt = Date.now();
    this.firstL3ChunkLogged = false;
    this.firstTtsTextLogged = false;
    this.maxSpeakSentences = opts?.maxSpeakSentences ?? 3;
    this.companionUi = opts?.companionUi ?? true;
    this.ttsVoice = (opts?.ttsVoice || DEFAULT_KOKORO_TTS_VOICE).trim() || DEFAULT_KOKORO_TTS_VOICE;
    this.chunkChain = Promise.resolve();
    this.ttsChain = Promise.resolve();
    this.generation = voicePlaybackController.bumpGeneration();
    if (this.companionUi) {
      voiceSessionStore.setState("listening");
      void notifyCompanionVoicePhase("listening");
    }
    voiceCompanionDebug("orchestrator.start_session", {
      sessionId,
      generation: this.generation,
      maxSpeakSentences: this.maxSpeakSentences,
      companionUi: this.companionUi,
      ttsVoice: this.ttsVoice ?? "",
    });
    voiceChatTraceIfActive("tts.orchestrator.start", {
      sessionId,
      generation: this.generation,
      maxSpeakSentences: this.maxSpeakSentences,
      companionUi: this.companionUi,
      ttsVoice: this.ttsVoice ?? "",
    });
  }

  onL3Thinking(): void {
    void warmJvsAudioModels({ stt: false, tts: true, sv: false, reason: "l3_thinking" }).catch((e) => {
      voiceCompanionDebug("orchestrator.tts_prewarm_warn", { sessionId: this.sessionId, err: String(e) });
      voiceChatTraceIfActive("tts.orchestrator.prewarm_warn", { sessionId: this.sessionId, err: String(e) });
    });
    if (!this.companionUi) return;
    voiceSessionStore.setState("thinking");
    void notifyCompanionVoicePhase("thinking");
    voiceCompanionDebug("orchestrator.thinking", { sessionId: this.sessionId });
  }

  onL3Chunk(chunk: string, onSentence?: ChunkConsumer): Promise<void> {
    chunk = stripAssistantUiProtocol(chunk);
    if (!chunk.trim()) return Promise.resolve();
    if (this.maxSpeakSentences <= 0) return Promise.resolve();
    if (chunk === this.lastChunkIn) {
      voiceCompanionDebug("orchestrator.chunk_skip_dup", { chunk: truncVoiceLog(chunk, 40) });
      return Promise.resolve();
    }
    this.lastChunkIn = chunk;
    voiceCompanionDebug("orchestrator.chunk_in", {
      sessionId: this.sessionId,
      generation: this.generation,
      chunk: truncVoiceLog(chunk, 80),
      len: chunk.length,
    });
    voiceChatTraceIfActive("tts.orchestrator.chunk", {
      sessionId: this.sessionId,
      chunk: truncVoiceLog(chunk, 120),
      len: chunk.length,
    });
    if (!this.firstL3ChunkLogged) {
      this.firstL3ChunkLogged = true;
      voiceChatTraceIfActive("llm_first_token_ms", {
        latencyMs: Math.max(0, Date.now() - this.sessionStartedAt),
        sessionId: this.sessionId,
        chunk: truncVoiceLog(chunk, 120),
        len: chunk.length,
      });
    }
    this.chunkChain = this.chunkChain.then(() =>
      this.processChunk(chunk, onSentence),
    );
    return this.chunkChain;
  }

  finishStream(onSentence?: ChunkConsumer): Promise<void> {
    voiceCompanionDebug("orchestrator.finish_stream", {
      sessionId: this.sessionId,
      remainder: truncVoiceLog(this.sentenceRemainder, 80),
    });
    voiceChatTraceIfActive("tts.orchestrator.finish_stream", {
      sessionId: this.sessionId,
      remainder: truncVoiceLog(this.sentenceRemainder, 120),
    });
    this.chunkChain = this.chunkChain.then(() =>
      this.processFinish(onSentence),
    );
    return this.chunkChain;
  }

  speakCue(text: string, reason = "assistant_cue"): Promise<void> {
    const cue = text.trim();
    if (!cue) return Promise.resolve();
    this.scheduleSpeakSentence(cue, undefined, "cue", reason);
    return this.ttsChain;
  }

  private scheduleSpeakSentence(
    sentence: string,
    onSentence?: ChunkConsumer,
    kind: SpeechJobKind = "content",
    reason?: string,
    sentenceUnits = this.countHardSentenceUnits(sentence),
  ): void {
    const job: SpeechJobContext = {
      generation: this.generation,
      sessionId: this.sessionId,
      ttsVoice: this.ttsVoice,
      companionUi: this.companionUi,
      queuedAt: Date.now(),
      kind,
      reason,
      sentenceUnits,
    };
    if (!this.firstTtsTextLogged) {
      this.firstTtsTextLogged = true;
      voiceChatTraceIfActive("tts_first_text_ms", {
        latencyMs: Math.max(0, Date.now() - this.sessionStartedAt),
        sessionId: this.sessionId,
        reason: reason ?? "",
        chars: sentence.trim().length,
        text: truncVoiceLog(sentence, 160),
      });
    }
    this.ttsChain = this.ttsChain
      .then(() => this.speakSentence(sentence, onSentence, job))
      .catch((e) => {
        voiceCompanionDebug("orchestrator.tts_chain_fail", { err: String(e) });
        voiceChatTraceIfActive("tts.orchestrator.chain_fail", { err: String(e) });
      });
  }

  private async waitForSpeechDrain(): Promise<void> {
    await this.ttsChain;
    await voicePlaybackController.waitForIdle();
  }

  private async speakSentence(
    sentence: string,
    onSentence: ChunkConsumer | undefined,
    job: SpeechJobContext,
  ): Promise<void> {
    if (job.generation !== voicePlaybackController.getGeneration()) return;
    const dequeueAt = Date.now();
    const queueWaitMs = dequeueAt - job.queuedAt;
    voiceChatTraceIfActive("tts.orchestrator.dequeue", {
      sessionId: job.sessionId,
      generation: job.generation,
      queueWaitMs,
      raw: truncVoiceLog(sentence, 120),
      kind: job.kind,
      reason: job.reason ?? "",
    });
    if (job.kind === "content" && this.maxSpeakSentences <= 0) return;
    if (job.kind === "content" && this.spokenSentenceCount >= this.maxSpeakSentences) {
      voiceCompanionDebug("orchestrator.tts_skip_cap", {
        cap: this.maxSpeakSentences,
        raw: truncVoiceLog(sentence, 60),
      });
      return;
    }
    const prepared = prepareSentenceForTtsDetailed(sentence);
    const speakable = prepared.text;
    if (!speakable) {
      voiceCompanionDebug("orchestrator.tts_skip_unspeakable", {
        raw: truncVoiceLog(sentence, 80),
        normalized: truncVoiceLog(prepared.normalizedText, 120),
        skipReason: prepared.skipReason ?? "",
        matchedRule: prepared.matchedRule ?? "",
      });
      voiceChatTraceIfActive("tts.orchestrator.skip_unspeakable", {
        raw: truncVoiceLog(sentence, 120),
        normalized: truncVoiceLog(prepared.normalizedText, 160),
        skipReason: prepared.skipReason ?? "",
        matchedRule: prepared.matchedRule ?? "",
      });
      return;
    }
    const dedupeKey = this.sentenceDedupeKey(speakable);
    if (dedupeKey && this.spokenOrQueuedSentenceKeys.has(dedupeKey)) {
      voiceCompanionDebug("orchestrator.tts_skip_duplicate", {
        sentence: truncVoiceLog(speakable, 80),
        key: truncVoiceLog(dedupeKey, 80),
      });
      voiceChatTraceIfActive("tts.orchestrator.skip_duplicate", {
        sentence: truncVoiceLog(speakable, 120),
      });
      return;
    }
    if (dedupeKey) this.spokenOrQueuedSentenceKeys.add(dedupeKey);

    const jobGeneration = job.generation;
    const sessionId = job.sessionId;
    const ttsVoice = (job.ttsVoice || DEFAULT_KOKORO_TTS_VOICE).trim() || DEFAULT_KOKORO_TTS_VOICE;
    const useMandarinNeuralVoice = false;
    const ttsRequestKind = this.ttsRequestKindFor(speakable, job);
    onSentence?.(speakable);
    if (job.kind === "content" && this.isHardSentenceBoundary(speakable)) {
      this.spokenSentenceCount += Math.max(1, job.sentenceUnits);
    }
    voiceCompanionDebug("orchestrator.tts_request", {
      sentence: truncVoiceLog(speakable, 120),
      sessionId,
      generation: jobGeneration,
      spoken: this.spokenSentenceCount,
      sentenceUnits: job.sentenceUnits,
      kind: job.kind,
      ttsRequestKind,
      reason: job.reason ?? "",
    });
    voiceChatTraceIfActive("tts.orchestrator.request", {
      sentence: truncVoiceLog(speakable, 200),
      spokenIndex: this.spokenSentenceCount + 1,
      sentenceUnits: job.sentenceUnits,
      sessionId,
      queueWaitMs,
      kind: job.kind,
      ttsRequestKind,
      reason: job.reason ?? "",
    });
    try {
      const synthStartedAt = Date.now();
      if (!useMandarinNeuralVoice) {
        let streamStarted = false;
        try {
          voicePlaybackController.beginPcmStream(jobGeneration);
          streamStarted = true;
          let firstStreamChunk = true;
          let firstPlayLogged = false;
          const streamResult = await streamSynthesizeByJvs(
            speakable,
            ttsVoice,
            sessionId,
            ttsRequestKind,
            async (chunk, meta) => {
              if (jobGeneration !== voicePlaybackController.getGeneration()) return;
              if (firstStreamChunk) {
                firstStreamChunk = false;
                if (job.companionUi) {
                  voiceSessionStore.setState("speaking");
                  void notifyCompanionVoicePhase("speaking");
                }
              }
              const playInfo = await voicePlaybackController.enqueuePcm16Chunk(chunk, {
                sampleRate: meta.sampleRate,
                channels: meta.channels,
                generation: jobGeneration,
              });
              if (!firstPlayLogged && playInfo) {
                firstPlayLogged = true;
                voiceChatTraceIfActive("tts_first_play_ms", {
                  latencyMs: Date.now() - synthStartedAt + playInfo.scheduledMs,
                  localScheduleMs: playInfo.scheduledMs,
                  sampleRate: playInfo.sampleRate,
                  channels: playInfo.channels,
                  durationMs: playInfo.durationMs,
                  textLen: speakable.length,
                  kind: ttsRequestKind,
                  sessionId,
                });
              }
            },
            {
              firstAudioSlowMs: TTS_FIRST_AUDIO_SLOW_MS,
              failOnFirstAudioSlow: false,
            },
          );
          await voicePlaybackController.endPcmStream(jobGeneration, { waitForPlayback: false });
          streamStarted = false;
          if (streamResult.ok && streamResult.chunks > 0) {
            const synthMs = Date.now() - synthStartedAt;
            voiceCompanionDebug("orchestrator.tts_ok", {
              bytes: streamResult.bytes,
              sentence: truncVoiceLog(speakable, 80),
              generation: jobGeneration,
              engine: "jvs_cloud_stream",
              voice: ttsVoice ?? "",
              ttsRequestKind,
              synthMs,
              queueWaitMs,
              firstAudioMs: streamResult.firstAudioMs,
              chunks: streamResult.chunks,
            });
            voiceChatTraceIfActive("tts.orchestrator.ok", {
              bytes: streamResult.bytes,
              sentence: truncVoiceLog(speakable, 120),
              latencyMs: synthMs,
              queueWaitMs,
              engine: "jvs_cloud_stream",
              voice: ttsVoice ?? "",
              ttsRequestKind,
              firstAudioMs: streamResult.firstAudioMs,
              chunks: streamResult.chunks,
              format: streamResult.format,
              model: streamResult.model,
            });
            return;
          }
        } catch (e) {
          voiceCompanionDebug("orchestrator.tts_stream_fallback", {
            err: String(e),
            sentence: truncVoiceLog(speakable, 80),
          });
          voiceChatTraceIfActive("tts.orchestrator.stream_fallback", {
            err: String(e),
            sentence: truncVoiceLog(speakable, 120),
          });
        } finally {
          if (streamStarted) {
            await voicePlaybackController.endPcmStream(jobGeneration, { waitForPlayback: false }).catch(() => undefined);
          }
        }
      }
      const blob = (
        useMandarinNeuralVoice
          ? await synthesizeSpeechL2Only(speakable, ttsVoice)
          : await synthesizeByJvs(speakable, ttsVoice, sessionId, ttsRequestKind)
      );
      const synthMs = Date.now() - synthStartedAt;
      if (jobGeneration !== voicePlaybackController.getGeneration()) {
        voiceCompanionDebug("orchestrator.tts_skip_stale", {
          sentence: truncVoiceLog(speakable, 80),
          jobGeneration,
          currentGeneration: voicePlaybackController.getGeneration(),
        });
        return;
      }
      voiceCompanionDebug("orchestrator.tts_ok", {
        bytes: blob.size,
        sentence: truncVoiceLog(speakable, 80),
        generation: jobGeneration,
        engine: useMandarinNeuralVoice ? "l2_edge" : "jvs_kokoro",
        voice: ttsVoice ?? "",
        ttsRequestKind,
        synthMs,
        queueWaitMs,
      });
      voiceChatTraceIfActive("tts.orchestrator.ok", {
        bytes: blob.size,
        sentence: truncVoiceLog(speakable, 120),
        latencyMs: synthMs,
        queueWaitMs,
        engine: useMandarinNeuralVoice ? "l2_edge" : "jvs_kokoro",
        voice: ttsVoice ?? "",
        ttsRequestKind,
      });
      if (job.companionUi) {
        voiceSessionStore.setState("speaking");
        void notifyCompanionVoicePhase("speaking");
      }
      const enqueueStartedAt = Date.now();
      voiceChatTraceIfActive("tts.orchestrator.enqueue_start", {
        bytes: blob.size,
        generation: jobGeneration,
        sentence: truncVoiceLog(speakable, 120),
      });
      await voicePlaybackController.enqueue(blob, jobGeneration);
      voiceChatTraceIfActive("tts.orchestrator.enqueue_done", {
        bytes: blob.size,
        generation: jobGeneration,
        latencyMs: Date.now() - enqueueStartedAt,
      });
    } catch (e) {
      // 为保持音色一致性，不再自动回退到 L2 另一套 TTS 音色，避免“每句一个人”。
      voiceCompanionDebug("orchestrator.tts_fail", {
        err: String(e),
        sentence: truncVoiceLog(speakable, 80),
      });
      voiceChatTraceIfActive("tts.orchestrator.fail", {
        err: String(e),
        sentence: truncVoiceLog(speakable, 120),
      });
      console.warn("[VoiceOrchestrator] TTS sentence failed (JVS/L2):", e);
    }
  }

  private async processChunk(chunk: string, onSentence?: ChunkConsumer): Promise<void> {
    const split = splitSentences(this.sentenceRemainder, chunk);
    this.sentenceRemainder = split.remainder;
    for (const sentence of split.complete) {
      this.queueContentSentence(sentence, onSentence);
    }
  }

  private async processFinish(onSentence?: ChunkConsumer): Promise<void> {
    const tail = this.sentenceRemainder.trim();
    this.sentenceRemainder = "";
    const finishGeneration = this.generation;
    if (tail) {
      this.queueContentSentence(tail, onSentence);
    }
    this.flushPendingContentSegments();
    await this.waitForSpeechDrain();
    if (this.companionUi && finishGeneration === voicePlaybackController.getGeneration()) {
      voiceSessionStore.setState("idle");
      void notifyCompanionVoicePhase("idle");
    }
    voiceCompanionDebug("orchestrator.stream_idle", { sessionId: this.sessionId });
    voiceChatTraceIfActive("tts.orchestrator.idle", { sessionId: this.sessionId });
  }

  async bargeIn(): Promise<void> {
    voiceCompanionDebug("orchestrator.barge_in", { sessionId: this.sessionId });
    await voicePlaybackController.stopAndReset();
    voicePlaybackController.clearQueue();
    this.clearPendingContentFlushTimer();
    this.pendingContentSegments = [];
    this.generation = voicePlaybackController.bumpGeneration();
    this.ttsChain = Promise.resolve();
    void cancelJvsSession(this.sessionId).catch((e) => {
      voiceCompanionDebug("orchestrator.cancel_jvs_fail", { err: String(e) });
    });
    if (this.companionUi) {
      voiceSessionStore.setState("listening");
      void notifyCompanionVoicePhase("listening");
    }
  }

  private queueContentSentence(sentence: string, onSentence?: ChunkConsumer): void {
    const text = sentence.trim();
    if (!text) return;
    this.pendingContentSegments.push({
      text,
      onSentence,
      units: this.countHardSentenceUnits(text),
    });
    const merged = this.pendingContentSegments.map((item) => item.text).join("");
    const units = this.pendingContentSegments.reduce((sum, item) => sum + item.units, 0);
    const isImmediatePhrase = this.isHardSentenceBoundary(text);
    if (isImmediatePhrase || merged.length >= CONTENT_COALESCE_MAX_CHARS || units >= this.maxSpeakSentences) {
      voiceChatTraceIfActive("tts.orchestrator.flush_ready", {
        reason: isImmediatePhrase ? "sentence_boundary" : "coalesce_limit",
        chars: merged.length,
        units,
        text: truncVoiceLog(merged, 160),
      });
      this.flushPendingContentSegments();
      return;
    }
    if (!this.pendingContentFlushTimer) {
      this.pendingContentFlushTimer = setTimeout(() => {
        this.pendingContentFlushTimer = null;
        this.flushPendingContentSegments();
      }, CONTENT_COALESCE_DELAY_MS);
    }
  }

  private flushPendingContentSegments(): void {
    if (this.pendingContentSegments.length === 0) return;
    this.clearPendingContentFlushTimer();
    const segments = this.pendingContentSegments.splice(0);
    const merged = segments.map((item) => item.text).join("");
    const units = segments.reduce((sum, item) => sum + item.units, 0);
    const onSentence = segments[segments.length - 1]?.onSentence;
    voiceChatTraceIfActive("tts.orchestrator.coalesce", {
      segments: segments.length,
      units,
      chars: merged.length,
      text: truncVoiceLog(merged, 200),
    });
    const leadSplit = this.splitCloudLead(merged);
    if (leadSplit) {
      this.scheduleSpeakSentence(leadSplit[0], undefined, "cue", "cloud_fast_lead", 1);
      this.scheduleSpeakSentence(leadSplit[1], onSentence, "content", "cloud_base_remainder", Math.max(1, units - 1));
      return;
    }
    this.scheduleSpeakSentence(merged, onSentence, "content", "coalesced_content", units);
  }

  private clearPendingContentFlushTimer(): void {
    if (!this.pendingContentFlushTimer) return;
    clearTimeout(this.pendingContentFlushTimer);
    this.pendingContentFlushTimer = null;
  }
}

export const voiceOrchestrator = new VoiceOrchestrator();
