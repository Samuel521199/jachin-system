/**
 * 桌面 Voice Core — STT/TTS 统一入口（JVS + L3，不依赖 L2 :18888 voice API）
 * @see clients/desktop/docs/VOICE_UNIFIED_PIPELINE_PROPOSAL.md
 */

import { getJvsHealth, startJvsProcess, transcribeByJvs } from "./voiceBridge";
import type { VoiceUxProfile } from "./voiceProfiles";
import { voiceChatTrace, voiceChatTraceIfActive } from "./voiceChatTraceLog";

export class VoiceServiceError extends Error {
  constructor(
    message: string,
    readonly code: "jvs" | "stt" | "l3" | "mic" | "clarification" | "unknown" = "unknown",
    readonly details?: Record<string, unknown>,
  ) {
    super(message);
    this.name = "VoiceServiceError";
  }
}

export const VOICE_UNAVAILABLE_HINT =
  "语音服务不可用：请确认 JVS（http://127.0.0.1:18982）与 Layer 3（ws://127.0.0.1:18981）已启动。";

export interface VoiceTranscriptionResult {
  text: string;
  rawText: string;
  correctedText: string;
  userMessage: string;
  userMessageSource?: string;
  replyPlan?: Record<string, unknown>;
  confidence: number;
  durationMs: number;
  language: string;
  backend?: string;
  understanding?: unknown;
  source?: "jvs_http_transcribe" | "jvs_ws_final" | "jvs_stream_ws" | string;
  finalized?: boolean;
  provisional?: boolean;
  streamText?: string;
  hotwordCount?: number;
  hotwordStatus?: string;
  hotwordSources?: string[];
  hotwordDominated?: boolean;
}

export interface VoiceTranscriptionOptions {
  signal?: AbortSignal;
  timeoutMs?: number;
}

function sanitizeSttText(text: string): string {
  const cleaned = (text || "")
    .replace(/<\|.*?\|>/g, "")
    .replace(/\s+/g, " ")
    .trim();
  return /[\u4e00-\u9fffA-Za-z0-9]/.test(cleaned) ? cleaned : "";
}

function encodePcm16Wav(samples: Int16Array, sampleRate: number): ArrayBuffer {
  const dataLen = samples.length * 2;
  const buffer = new ArrayBuffer(44 + dataLen);
  const view = new DataView(buffer);
  const writeStr = (offset: number, s: string) => {
    for (let i = 0; i < s.length; i++) view.setUint8(offset + i, s.charCodeAt(i));
  };
  writeStr(0, "RIFF");
  view.setUint32(4, 36 + dataLen, true);
  writeStr(8, "WAVE");
  writeStr(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeStr(36, "data");
  view.setUint32(40, dataLen, true);
  for (let i = 0; i < samples.length; i++) {
    view.setInt16(44 + i * 2, samples[i], true);
  }
  return buffer;
}

/** Rust STT_AUDIO_READY 已是 WAV；浏览器录音需转 16kHz mono WAV */
export async function ensureWav16kMonoBlob(blob: Blob): Promise<Blob> {
  if (blob.type.includes("wav") && blob.size > 44) {
    return blob;
  }
  const arrayBuffer = await blob.arrayBuffer();
  const audioCtx = new AudioContext({ sampleRate: 16000 });
  try {
    const decoded = await audioCtx.decodeAudioData(arrayBuffer.slice(0));
    const { length, numberOfChannels, sampleRate } = decoded;
    const mono = new Float32Array(length);
    for (let c = 0; c < numberOfChannels; c++) {
      const ch = decoded.getChannelData(c);
      for (let i = 0; i < length; i++) mono[i] += ch[i] / numberOfChannels;
    }
    let pcm = mono;
    if (sampleRate !== 16000) {
      const newLen = Math.max(1, Math.round((length * 16000) / sampleRate));
      const resampled = new Float32Array(newLen);
      for (let i = 0; i < newLen; i++) {
        const srcIdx = (i * sampleRate) / 16000;
        const idx = Math.floor(srcIdx);
        const frac = srcIdx - idx;
        const a = mono[idx] ?? 0;
        const b = mono[Math.min(idx + 1, length - 1)] ?? a;
        resampled[i] = a + frac * (b - a);
      }
      pcm = resampled;
    }
    const int16 = new Int16Array(pcm.length);
    for (let i = 0; i < pcm.length; i++) {
      const s = Math.max(-1, Math.min(1, pcm[i]));
      int16[i] = s < 0 ? s * 32768 : s * 32767;
    }
    return new Blob([encodePcm16Wav(int16, 16000)], { type: "audio/wav" });
  } finally {
    await audioCtx.close();
  }
}

export function wavBase64ToBlob(wavBase64: string): Blob {
  const bytes = Uint8Array.from(atob(wavBase64), (c) => c.charCodeAt(0));
  return new Blob([bytes], { type: "audio/wav" });
}

export async function ensureJvsReady(): Promise<void> {
  const health = await getJvsHealth().catch(() => null);
  if (!health?.ok) {
    await startJvsProcess();
  }
}

export async function transcribeBlobDetailed(
  audioBlob: Blob,
  profile: VoiceUxProfile = "chat_ptt",
  options: VoiceTranscriptionOptions = {},
): Promise<VoiceTranscriptionResult> {
  const wavBytes = audioBlob.size;
  const pipelineStartedAt = Date.now();
  voiceChatTraceIfActive("stt.prepare", { profile, wavBytes, blobType: audioBlob.type });
  try {
    const wavStartedAt = Date.now();
    const wavBlob = await ensureWav16kMonoBlob(audioBlob);
    voiceChatTraceIfActive("stt.wav_ready", {
      profile,
      inBytes: wavBytes,
      outBytes: wavBlob.size,
      latencyMs: Date.now() - wavStartedAt,
    });
    const healthStartedAt = Date.now();
    voiceChatTraceIfActive("stt.jvs_health_check", { profile });
    await ensureJvsReady();
    voiceChatTraceIfActive("stt.jvs_ready", {
      profile,
      latencyMs: Date.now() - healthStartedAt,
    });
    voiceChatTraceIfActive("stt.jvs_transcribe_request", {
      profile,
      wavBytes: wavBlob.size,
    });
    const sttStarted = Date.now();
    const stt = await transcribeByJvs(wavBlob, {
      signal: options.signal,
      timeoutMs: options.timeoutMs,
    });
    const correctedText = (stt.text || "").trim();
    const rawText = (stt.raw_text || stt.text || "").trim();
    const userMessage = "";
    const replyPlan = {} as Record<string, unknown>;
    const text = sanitizeSttText(correctedText);
    const understanding = stt.understanding ?? {};
    const cloudDiagnostics = Array.isArray((understanding as any).cloud_diagnostics)
      ? ((understanding as any).cloud_diagnostics as Array<Record<string, unknown>>)
      : [];
    const sttOrchestration = Array.isArray((understanding as any).stt_orchestration)
      ? ((understanding as any).stt_orchestration as Array<Record<string, unknown>>)
      : [];
    for (const event of cloudDiagnostics) {
      const stage = String(event.stage || "cloud_diagnostic");
      voiceChatTraceIfActive(`stt.${stage}`, {
        profile,
        source: "jvs_server_cloud_diagnostics",
        ...event,
      });
    }
    for (const event of sttOrchestration) {
      const stage = String(event.stage || "stt_orchestration");
      voiceChatTraceIfActive(`stt.${stage}`, {
        profile,
        source: "jvs_server_orchestration",
        ...event,
      });
    }
    voiceChatTraceIfActive("stt.jvs_transcribe_ok", {
      profile,
      text,
      rawText,
      correctedText,
      userMessage,
      userMessageSource: "",
      replyPlan,
      confidence: stt.confidence,
      durationMs: stt.duration_ms,
      language: stt.language,
      backend: stt.backend,
      understanding,
      hotwordCount: stt.hotword_count,
      hotwordStatus: stt.hotword_status,
      hotwordSources: stt.hotword_sources,
      latencyMs: Date.now() - sttStarted,
      pipelineMs: Date.now() - pipelineStartedAt,
    });
    if (String(stt.backend || "").includes("fallback_from_cloud") || understanding.stt_fallback) {
      voiceChatTraceIfActive("stt.local_fallback_used", {
        profile,
        backend: stt.backend,
        fallback: understanding.stt_fallback,
        latencyMs: Date.now() - sttStarted,
        pipelineMs: Date.now() - pipelineStartedAt,
      });
    }
    if (!text || text.startsWith("【STT错误】")) {
      voiceChatTraceIfActive("stt.jvs_empty_or_error", { profile, raw: text });
      throw new VoiceServiceError(text || "未能识别语音内容，请重试", "stt");
    }
    return {
      text,
      rawText,
      correctedText,
      userMessage,
      confidence: stt.confidence,
      durationMs: stt.duration_ms,
      language: stt.language,
      backend: stt.backend,
      understanding,
      source: "jvs_http_transcribe",
      finalized: true,
      provisional: false,
      hotwordCount: stt.hotword_count,
      hotwordStatus: stt.hotword_status,
      hotwordSources: stt.hotword_sources,
    };
  } catch (e) {
    if (e instanceof VoiceServiceError) {
      voiceChatTraceIfActive("stt.fail", {
        profile,
        code: e.code,
        error: e.message,
        pipelineMs: Date.now() - pipelineStartedAt,
      });
      throw e;
    }
    const msg = e instanceof Error ? e.message : String(e);
    if (msg.startsWith("JVS_STT_TIMEOUT:")) {
      const timeoutMs = Number(msg.split(":")[1] || 0);
      voiceChatTraceIfActive("stt.jvs_transcribe_timeout", {
        profile,
        timeoutMs,
        pipelineMs: Date.now() - pipelineStartedAt,
      });
      throw new VoiceServiceError("语音识别服务响应超时，已经中止这轮识别。请再说一次。", "stt", {
        reason: "jvs_stt_timeout",
        timeoutMs,
      });
    }
    if (msg === "JVS_STT_ABORTED") {
      voiceChatTraceIfActive("stt.jvs_transcribe_aborted", {
        profile,
        pipelineMs: Date.now() - pipelineStartedAt,
      });
      throw new VoiceServiceError("这轮语音识别已被新的语音输入取消。", "stt", {
        reason: "jvs_stt_aborted",
      });
    }
    voiceChatTraceIfActive("stt.fail", {
      profile,
      code: "unknown",
      error: msg,
      pipelineMs: Date.now() - pipelineStartedAt,
    });
    if (msg.toLowerCase().includes("fetch") || msg.toLowerCase().includes("network")) {
      throw new VoiceServiceError(VOICE_UNAVAILABLE_HINT, "jvs");
    }
    throw new VoiceServiceError(msg, "jvs");
  }
}
export async function transcribeBlob(audioBlob: Blob, profile: VoiceUxProfile = "chat_ptt"): Promise<string> {
  return (await transcribeBlobDetailed(audioBlob, profile)).text;
}

export async function transcribeWavBase64Detailed(
  wavBase64: string,
  profile: VoiceUxProfile = "chat_vad",
  options: VoiceTranscriptionOptions = {},
): Promise<VoiceTranscriptionResult> {
  return transcribeBlobDetailed(wavBase64ToBlob(wavBase64), profile, options);
}

export async function transcribeWavBase64(wavBase64: string, profile: VoiceUxProfile = "chat_vad"): Promise<string> {
  return (await transcribeWavBase64Detailed(wavBase64, profile)).text;
}

export function formatVoiceUserMessage(text: string, profile: VoiceUxProfile): string {
  const t = text.trim();
  if (!t) return t;
  if (profile === "wake") return t;
  return `🎤 ${t}`;
}
