import { invoke } from "@tauri-apps/api/core";
import { truncVoiceLog, voiceCompanionDebug } from "./voiceCompanionDebugLog";
import { voiceChatTraceIfActive } from "./voiceChatTraceLog";
import { DEFAULT_KOKORO_TTS_SPEED } from "./voiceDefaults";

export type JvsStatus = {
  running: boolean;
  autoSpawnEnabled: boolean;
  baseUrl: string;
  host: string;
  port: number;
  modelRoot: string;
  lastError?: string | null;
};

const JVS_BASE =
  import.meta.env.DEV
    ? "/jvs"
    : import.meta.env.VITE_JVS_BASE_URL || "http://127.0.0.1:18982";

const DEFAULT_STT_TIMEOUT_MS = Number(import.meta.env.VITE_JVS_STT_TIMEOUT_MS || 30000);

export type JvsTranscribeOptions = {
  sessionId?: string;
  signal?: AbortSignal;
  timeoutMs?: number;
};

function jvsWsBase(): string {
  const explicit = import.meta.env.VITE_JVS_WS_BASE_URL;
  if (explicit) return String(explicit).replace(/\/+$/, "");
  if (import.meta.env.DEV || JVS_BASE === "/jvs") return "ws://127.0.0.1:18982";
  if (JVS_BASE.startsWith("https://")) return `wss://${JVS_BASE.slice("https://".length)}`.replace(/\/+$/, "");
  if (JVS_BASE.startsWith("http://")) return `ws://${JVS_BASE.slice("http://".length)}`.replace(/\/+$/, "");
  return "ws://127.0.0.1:18982";
}

function base64ToArrayBuffer(b64: string): ArrayBuffer {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}

export async function startJvsProcess(): Promise<JvsStatus> {
  return invoke<JvsStatus>("jvs_start");
}

export async function stopJvsProcess(): Promise<JvsStatus> {
  return invoke<JvsStatus>("jvs_stop");
}

export async function getJvsStatus(): Promise<JvsStatus> {
  return invoke<JvsStatus>("jvs_status");
}

export async function getJvsHealth(): Promise<{ ok: boolean; base_url: string }> {
  return invoke<{ ok: boolean; base_url: string }>("jvs_health");
}

export async function warmJvsAudioModels(opts: { stt?: boolean; tts?: boolean; sv?: boolean; reason?: string } = {}): Promise<void> {
  const res = await fetch(`${JVS_BASE}/v1/models/audio/warm`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      stt: opts.stt ?? true,
      tts: opts.tts ?? true,
      sv: opts.sv ?? false,
      reason: opts.reason,
    }),
  });
  if (!res.ok) {
    throw new Error(await res.text());
  }
}
export async function transcribeByJvs(audioBlob: Blob, sessionIdOrOptions?: string | JvsTranscribeOptions): Promise<{
  text: string;
  raw_text?: string;
  user_message?: string;
  user_message_source?: string;
  reply_plan?: Record<string, unknown>;
  confidence: number;
  duration_ms: number;
  language: string;
  backend?: string;
  hotword_count?: number;
  hotword_status?: string;
  hotword_sources?: string[];
  understanding?: {
    selected?: {
      type?: string;
      intent?: string;
      slots?: Record<string, string>;
      missing_slots?: string[];
      question?: string;
      corrected_text?: string;
      can_execute?: boolean;
      score?: number;
    };
    [key: string]: unknown;
  };
}> {
  const options: JvsTranscribeOptions =
    typeof sessionIdOrOptions === "string" ? { sessionId: sessionIdOrOptions } : sessionIdOrOptions || {};
  const timeoutMs = Math.max(3000, Number(options.timeoutMs || DEFAULT_STT_TIMEOUT_MS || 30000));
  const form = new FormData();
  form.append("audio", audioBlob, "speech.wav");
  if (options.sessionId) form.append("session_id", options.sessionId);

  const controller = new AbortController();
  let timedOut = false;
  const timeout = window.setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  const abortFromCaller = () => controller.abort();
  if (options.signal) {
    if (options.signal.aborted) {
      window.clearTimeout(timeout);
      throw new Error("JVS_STT_ABORTED");
    }
    options.signal.addEventListener("abort", abortFromCaller, { once: true });
  }

  try {
    const res = await fetch(`${JVS_BASE}/v1/stt/transcribe`, {
      method: "POST",
      body: form,
      signal: controller.signal,
    });
    if (!res.ok) {
      throw new Error(await res.text());
    }
    return res.json();
  } catch (err) {
    const name = err instanceof Error ? err.name : "";
    if (timedOut) {
      throw new Error(`JVS_STT_TIMEOUT:${timeoutMs}`);
    }
    if (controller.signal.aborted || name === "AbortError") {
      throw new Error("JVS_STT_ABORTED");
    }
    throw err;
  } finally {
    window.clearTimeout(timeout);
    options.signal?.removeEventListener("abort", abortFromCaller);
  }
}

export async function transcribeLocalByJvs(audioBlob: Blob, sessionIdOrOptions?: string | JvsTranscribeOptions): Promise<{
  text: string;
  raw_text?: string;
  user_message?: string;
  user_message_source?: string;
  reply_plan?: Record<string, unknown>;
  confidence: number;
  duration_ms: number;
  language: string;
  backend?: string;
  hotword_count?: number;
  hotword_status?: string;
  hotword_sources?: string[];
  understanding?: {
    selected?: {
      type?: string;
      intent?: string;
      slots?: Record<string, string>;
      missing_slots?: string[];
      question?: string;
      corrected_text?: string;
      can_execute?: boolean;
      score?: number;
    };
    [key: string]: unknown;
  };
}> {
  const options: JvsTranscribeOptions =
    typeof sessionIdOrOptions === "string" ? { sessionId: sessionIdOrOptions } : sessionIdOrOptions || {};
  const timeoutMs = Math.max(3000, Number(options.timeoutMs || DEFAULT_STT_TIMEOUT_MS || 30000));
  const form = new FormData();
  form.append("audio", audioBlob, "speech.wav");
  if (options.sessionId) form.append("session_id", options.sessionId);

  const controller = new AbortController();
  let timedOut = false;
  const timeout = window.setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  const abortFromCaller = () => controller.abort();
  if (options.signal) {
    if (options.signal.aborted) {
      window.clearTimeout(timeout);
      throw new Error("JVS_STT_ABORTED");
    }
    options.signal.addEventListener("abort", abortFromCaller, { once: true });
  }

  try {
    const res = await fetch(`${JVS_BASE}/v1/stt/transcribe_local`, {
      method: "POST",
      body: form,
      signal: controller.signal,
    });
    if (!res.ok) {
      throw new Error(await res.text());
    }
    return res.json();
  } catch (err) {
    const name = err instanceof Error ? err.name : "";
    if (timedOut) {
      throw new Error(`JVS_STT_TIMEOUT:${timeoutMs}`);
    }
    if (controller.signal.aborted || name === "AbortError") {
      throw new Error("JVS_STT_ABORTED");
    }
    throw err;
  } finally {
    window.clearTimeout(timeout);
    options.signal?.removeEventListener("abort", abortFromCaller);
  }
}

export async function synthesizeByJvs(text: string, voice?: string, sessionId?: string, kind: "content" | "cue" = "content"): Promise<Blob> {
  const url = `${JVS_BASE}/v1/tts/synthesize`;
  const startedAt = Date.now();
  voiceCompanionDebug("jvs.tts_fetch_start", {
    url,
    text: truncVoiceLog(text, 120),
    sessionId,
    voice,
    kind,
  });
  voiceChatTraceIfActive("tts.jvs_fetch_start", {
    url,
    text: truncVoiceLog(text, 160),
    textLen: text.length,
    sessionId,
    voice,
    kind,
  });
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      text,
      voice,
      session_id: sessionId,
      speed: DEFAULT_KOKORO_TTS_SPEED,
      kind,
    }),
  });
  const responseMs = Date.now() - startedAt;
  const audioDurationMs = Number(res.headers.get("X-Jachin-Duration-Ms") || 0);
  const serverSynthMs = Number(res.headers.get("X-Jachin-TTS-Synth-Ms") || 0);
  const attempts = Number(res.headers.get("X-Jachin-TTS-Attempts") || 0);
  const maxNewFrames = Number(res.headers.get("X-Jachin-TTS-Max-New-Frames") || 0);
  const quality = res.headers.get("X-Jachin-TTS-Quality") || "";
  const ttsKind = res.headers.get("X-Jachin-TTS-Kind") || "";
  const styleIndex = res.headers.get("X-Jachin-TTS-Style-Index") || "";
  const styleMode = res.headers.get("X-Jachin-TTS-Style-Mode") || "";
  const rawDurationMs = Number(res.headers.get("X-Jachin-TTS-Raw-Duration-Ms") || audioDurationMs);
  const trimLeadingMs = Number(res.headers.get("X-Jachin-TTS-Trim-Leading-Ms") || 0);
  const trimTrailingMs = Number(res.headers.get("X-Jachin-TTS-Trim-Trailing-Ms") || 0);
  voiceChatTraceIfActive("tts.jvs_fetch_response", {
    status: res.status,
    ok: res.ok,
    latencyMs: responseMs,
    serverSynthMs,
    audioDurationMs,
    attempts,
    maxNewFrames,
    quality,
    ttsKind,
    styleIndex,
    styleMode,
    rawDurationMs,
    trimLeadingMs,
    trimTrailingMs,
    sessionId,
    voice,
  });
  if (!res.ok) {
    const errText = await res.text();
    voiceCompanionDebug("jvs.tts_fetch_fail", { status: res.status, err: truncVoiceLog(errText, 200) });
    voiceChatTraceIfActive("tts.jvs_fetch_fail", {
      status: res.status,
      latencyMs: Date.now() - startedAt,
      err: truncVoiceLog(errText, 240),
    });
    throw new Error(errText);
  }
  const blobStartedAt = Date.now();
  const blob = await res.blob();
  const totalMs = Date.now() - startedAt;
  voiceCompanionDebug("jvs.tts_fetch_ok", { bytes: blob.size, type: blob.type });
  voiceChatTraceIfActive("tts.jvs_blob_ok", {
    bytes: blob.size,
    type: blob.type,
    blobReadMs: Date.now() - blobStartedAt,
    totalMs,
    serverSynthMs,
    audioDurationMs,
    attempts,
    maxNewFrames,
    quality,
    ttsKind,
    styleIndex,
    styleMode,
    rawDurationMs,
    trimLeadingMs,
    trimTrailingMs,
    sessionId,
    voice,
  });
  return blob;
}

export type JvsTtsStreamResult = {
  ok: boolean;
  unsupported?: boolean;
  firstAudioMs: number;
  totalMs: number;
  chunks: number;
  bytes: number;
  sampleRate: number;
  channels: number;
  format?: string;
  model?: string;
  voice?: string;
  requestId?: string;
};

export type JvsTtsStreamOptions = {
  firstAudioSlowMs?: number;
  failOnFirstAudioSlow?: boolean;
};

let ttsStreamUnavailable = false;

export async function streamSynthesizeByJvs(
  text: string,
  voice: string | undefined,
  sessionId: string | undefined,
  kind: "content" | "cue",
  onPcmChunk: (chunk: ArrayBuffer, meta: { sampleRate: number; channels: number; elapsedMs?: number }) => Promise<void> | void,
  options: JvsTtsStreamOptions = {},
): Promise<JvsTtsStreamResult> {
  if (ttsStreamUnavailable) {
    return { ok: false, unsupported: true, firstAudioMs: 0, totalMs: 0, chunks: 0, bytes: 0, sampleRate: 24000, channels: 1, format: "pcm_s16le" };
  }
  const url = `${jvsWsBase()}/v1/tts/stream`;
  const startedAt = Date.now();
  voiceCompanionDebug("jvs.tts_stream_start", { url, text: truncVoiceLog(text, 120), sessionId, voice, kind });
  voiceChatTraceIfActive("tts.jvs_stream_start", { url, text: truncVoiceLog(text, 160), textLen: text.length, sessionId, voice, kind });
  return new Promise<JvsTtsStreamResult>((resolve, reject) => {
    let settled = false;
    let sampleRate = 24000;
    let channels = 1;
    let chunks = 0;
    let bytes = 0;
    let firstAudioMs = 0;
    let requestId = "";
    let wsOpenMs = 0;
    let requestSentMs = 0;
    let metaMs = 0;
    let model = "";
    let resolvedVoice = "";
    let format = "pcm_s16le";
    const firstAudioSlowMs = Math.max(0, Number(options.firstAudioSlowMs || 0));
    const ws = new WebSocket(url);
    let firstAudioSlowTimer: number | null = null;
    const clearFirstAudioSlowTimer = () => {
      if (firstAudioSlowTimer === null) return;
      window.clearTimeout(firstAudioSlowTimer);
      firstAudioSlowTimer = null;
    };
    const rejectOnce = (err: Error) => {
      if (settled) return;
      settled = true;
      clearFirstAudioSlowTimer();
      try {
        ws.close();
      } catch {
        // ignore
      }
      reject(err);
    };
    const finish = (result: JvsTtsStreamResult) => {
      if (settled) return;
      settled = true;
      clearFirstAudioSlowTimer();
      try {
        ws.close();
      } catch {
        // ignore
      }
      resolve(result);
    };
    ws.onopen = () => {
      wsOpenMs = Date.now() - startedAt;
      voiceChatTraceIfActive("tts_ws_open_ms", {
        latencyMs: wsOpenMs,
        url,
        textLen: text.length,
        sessionId,
        voice,
        kind,
      });
      if (wsOpenMs > 500) {
        voiceChatTraceIfActive("tts_ws_open_slow", {
          latencyMs: wsOpenMs,
          thresholdMs: 500,
          url,
          textLen: text.length,
          sessionId,
          voice,
          kind,
        });
      }
      if (firstAudioSlowMs > 0) {
        firstAudioSlowTimer = window.setTimeout(() => {
          voiceChatTraceIfActive("tts_first_audio_slow", {
            thresholdMs: firstAudioSlowMs,
            latencyMs: Date.now() - startedAt,
            text: truncVoiceLog(text, 120),
            textLen: text.length,
            sessionId,
            voice,
            kind,
            reason: "first_audio_timeout",
          });
          if (options.failOnFirstAudioSlow) {
            rejectOnce(new Error("JVS_TTS_FIRST_AUDIO_TIMEOUT"));
          }
        }, firstAudioSlowMs);
      }
      ws.send(JSON.stringify({
        text,
        voice,
        session_id: sessionId,
        speed: DEFAULT_KOKORO_TTS_SPEED,
        kind,
      }));
      requestSentMs = Date.now() - startedAt;
      voiceChatTraceIfActive("tts_request_sent_ms", {
        latencyMs: requestSentMs,
        textLen: text.length,
        sessionId,
        voice,
        kind,
      });
    };
    ws.onerror = () => {
      if (settled) return;
      rejectOnce(new Error("JVS TTS stream websocket failed"));
    };
    ws.onmessage = async (ev) => {
      try {
        const msg = JSON.parse(String(ev.data || "{}"));
        if (msg.type === "open") {
          const cloudOpenMs = Number(msg.elapsed_ms || 0);
          voiceChatTraceIfActive("tts_cloud_ws_open_ms", {
            latencyMs: cloudOpenMs,
            thresholdMs: 500,
            textLen: text.length,
            sessionId,
            voice,
            kind,
          });
          if (cloudOpenMs > 500) {
            voiceChatTraceIfActive("tts_cloud_ws_open_slow", {
              latencyMs: cloudOpenMs,
              thresholdMs: 500,
              textLen: text.length,
              sessionId,
              voice,
              kind,
            });
          }
          return;
        }
        if (msg.type === "meta") {
          metaMs = Date.now() - startedAt;
          sampleRate = Number(msg.sample_rate || sampleRate);
          channels = Number(msg.channels || channels);
          model = String(msg.model || "");
          resolvedVoice = String(msg.voice || "");
          format = String(msg.format || format || "pcm_s16le");
          voiceChatTraceIfActive("tts_meta_ms", {
            latencyMs: metaMs,
            backend: msg.backend,
            model,
            voice: resolvedVoice,
            format,
            sampleRate,
            channels,
            connectionReuseSupported: Boolean(msg.connection_reuse_supported),
            poolReused: Boolean(msg.pool_reused),
            poolBorrowMs: Number(msg.pool_borrow_ms || 0),
            requestedVoice: voice,
            textLen: text.length,
            kind,
            sessionId,
          });
          voiceChatTraceIfActive("tts.jvs_stream_meta", {
            sampleRate,
            channels,
            backend: msg.backend,
            model,
            voice: resolvedVoice,
            format,
            connectionReuseSupported: Boolean(msg.connection_reuse_supported),
            poolReused: Boolean(msg.pool_reused),
            poolBorrowMs: Number(msg.pool_borrow_ms || 0),
            synthesisText: typeof msg.synthesis_text === "string" ? truncVoiceLog(msg.synthesis_text, 160) : "",
            textNormalized: Boolean(msg.text_normalized),
          });
          return;
        }
        if (msg.type === "audio" && msg.audio_b64) {
          const chunk = base64ToArrayBuffer(String(msg.audio_b64));
          chunks += 1;
          bytes += chunk.byteLength;
          if (firstAudioMs <= 0) {
            firstAudioMs = Date.now() - startedAt;
            clearFirstAudioSlowTimer();
            voiceChatTraceIfActive("tts_first_audio_ms", {
              latencyMs: firstAudioMs,
              wsOpenMs,
              requestSentMs,
              metaMs,
              model,
              voice: resolvedVoice || voice,
              format,
              sampleRate,
              channels,
              textLen: text.length,
              kind,
              sessionId,
            });
            if (firstAudioSlowMs > 0 && firstAudioMs > firstAudioSlowMs) {
              voiceChatTraceIfActive("tts_first_audio_slow", {
                thresholdMs: firstAudioSlowMs,
                latencyMs: firstAudioMs,
                text: truncVoiceLog(text, 120),
                textLen: text.length,
                sessionId,
                voice,
                kind,
                reason: "first_audio_arrived_slow",
              });
            }
            if (firstAudioMs > 3000) {
              voiceChatTraceIfActive("tts_first_audio_severe", {
                thresholdMs: 3000,
                latencyMs: firstAudioMs,
                text: truncVoiceLog(text, 120),
                textLen: text.length,
                sessionId,
                voice,
                kind,
                model,
                format,
              });
            }
          }
          await onPcmChunk(chunk, { sampleRate, channels, elapsedMs: Number(msg.elapsed_ms || 0) });
          return;
        }
        if (msg.type === "done") {
          requestId = String(msg.request_id || "");
          const totalMs = Date.now() - startedAt;
          voiceChatTraceIfActive("tts_total_ms", {
            latencyMs: totalMs,
            wsOpenMs,
            requestSentMs,
            metaMs,
            firstAudioMs,
            serverFirstPacketMs: Number(msg.first_packet_ms || 0),
            serverTotalMs: Number(msg.total_ms || 0),
            model,
            voice: resolvedVoice || voice,
            format,
            sampleRate,
            channels,
            textLen: text.length,
            kind,
            requestId,
            sessionId,
          });
          voiceCompanionDebug("jvs.tts_stream_done", { chunks, bytes, firstAudioMs, totalMs, requestId });
          voiceChatTraceIfActive("tts.jvs_stream_done", { chunks, bytes, firstAudioMs, totalMs, sampleRate, channels, format, model, voice: resolvedVoice || voice, requestId });
          finish({ ok: true, firstAudioMs, totalMs, chunks, bytes, sampleRate, channels, format, model, voice: resolvedVoice || voice, requestId });
          return;
        }
        if (msg.type === "error") {
          const code = String(msg.code || "");
          const message = String(msg.message || "JVS TTS stream error");
          if (code === "stream_unsupported" || code === "tts_not_ready") {
            ttsStreamUnavailable = true;
            finish({ ok: false, unsupported: true, firstAudioMs, totalMs: Date.now() - startedAt, chunks, bytes, sampleRate, channels, format, model, voice: resolvedVoice || voice });
            return;
          }
          rejectOnce(new Error(message));
        }
      } catch (e) {
        rejectOnce(e instanceof Error ? e : new Error(String(e)));
      }
    };
    ws.onclose = () => {
      if (!settled && chunks > 0) {
        finish({ ok: true, firstAudioMs, totalMs: Date.now() - startedAt, chunks, bytes, sampleRate, channels, format, model, voice: resolvedVoice || voice, requestId });
      } else if (!settled) {
        rejectOnce(new Error("JVS TTS stream closed before audio"));
      }
    };
  });
}

export async function cancelJvsSession(sessionId?: string): Promise<void> {
  const res = await fetch(`${JVS_BASE}/v1/session/cancel`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId }),
  });
  if (!res.ok) {
    throw new Error(await res.text());
  }
}
