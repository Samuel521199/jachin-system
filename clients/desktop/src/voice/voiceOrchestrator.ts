import { cancelJvsSession, synthesizeByJvs } from "./voiceBridge";
import { splitSentences } from "./sentenceBuffer";
import { prepareSentenceForTts } from "./speakableText";
import { voicePlaybackController } from "./voicePlaybackController";
import { voiceSessionStore } from "./voiceSessionStore";
import { truncVoiceLog, voiceCompanionDebug } from "./voiceCompanionDebugLog";
import { voiceChatTraceIfActive } from "./voiceChatTraceLog";
import { notifyCompanionVoicePhase } from "./voiceNativeBridge";
import { synthesizeSpeechL2Only } from "../lib/api";
import { DEFAULT_KOKORO_TTS_VOICE } from "./voiceDefaults";

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
};

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

  /**
   * 仅把“完整句”计入 maxSpeakSentences。
   * 逗号/顿号级分段用于加速首播，不应被当作一句而过早触发上限。
   */
  private isHardSentenceBoundary(text: string): boolean {
    const t = text.trim();
    if (!t) return false;
    const last = t[t.length - 1] || "";
    return /[。！？.!?]/.test(last);
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

  startSession(sessionId: string, opts?: VoiceOrchestratorSessionOpts): void {
    this.sessionId = sessionId;
    this.sentenceRemainder = "";
    this.lastChunkIn = "";
    this.spokenSentenceCount = 0;
    this.spokenOrQueuedSentenceKeys.clear();
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
    if (!this.companionUi) return;
    voiceSessionStore.setState("thinking");
    void notifyCompanionVoicePhase("thinking");
    voiceCompanionDebug("orchestrator.thinking", { sessionId: this.sessionId });
  }

  onL3Chunk(chunk: string, onSentence?: ChunkConsumer): Promise<void> {
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
  ): void {
    const job: SpeechJobContext = {
      generation: this.generation,
      sessionId: this.sessionId,
      ttsVoice: this.ttsVoice,
      companionUi: this.companionUi,
      queuedAt: Date.now(),
      kind,
      reason,
    };
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
    const speakable = prepareSentenceForTts(sentence);
    if (!speakable) {
      voiceCompanionDebug("orchestrator.tts_skip_unspeakable", {
        raw: truncVoiceLog(sentence, 80),
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
    onSentence?.(speakable);
    if (job.kind === "content" && this.isHardSentenceBoundary(speakable)) {
      this.spokenSentenceCount += 1;
    }
    voiceCompanionDebug("orchestrator.tts_request", {
      sentence: truncVoiceLog(speakable, 120),
      sessionId,
      generation: jobGeneration,
      spoken: this.spokenSentenceCount,
      kind: job.kind,
      reason: job.reason ?? "",
    });
    voiceChatTraceIfActive("tts.orchestrator.request", {
      sentence: truncVoiceLog(speakable, 200),
      spokenIndex: this.spokenSentenceCount + 1,
      sessionId,
      queueWaitMs,
      kind: job.kind,
      reason: job.reason ?? "",
    });
    try {
      const synthStartedAt = Date.now();
      const blob = useMandarinNeuralVoice
        ? await synthesizeSpeechL2Only(speakable, ttsVoice)
        : await synthesizeByJvs(speakable, ttsVoice, sessionId, job.kind);
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
      this.scheduleSpeakSentence(sentence, onSentence);
    }
  }

  private async processFinish(onSentence?: ChunkConsumer): Promise<void> {
    const tail = this.sentenceRemainder.trim();
    this.sentenceRemainder = "";
    const finishGeneration = this.generation;
    if (tail) {
      this.scheduleSpeakSentence(tail, onSentence);
    }
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
}

export const voiceOrchestrator = new VoiceOrchestrator();
