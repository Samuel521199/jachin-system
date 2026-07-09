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

export async function warmJvsAudioModels(opts: { stt?: boolean; tts?: boolean; sv?: boolean } = {}): Promise<void> {
  const res = await fetch(`${JVS_BASE}/v1/models/audio/warm`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      stt: opts.stt ?? true,
      tts: opts.tts ?? true,
      sv: opts.sv ?? false,
    }),
  });
  if (!res.ok) {
    throw new Error(await res.text());
  }
}
export async function transcribeByJvs(audioBlob: Blob, sessionId?: string): Promise<{
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
  const form = new FormData();
  form.append("audio", audioBlob, "speech.wav");
  if (sessionId) form.append("session_id", sessionId);
  const res = await fetch(`${JVS_BASE}/v1/stt/transcribe`, { method: "POST", body: form });
  if (!res.ok) {
    throw new Error(await res.text());
  }
  return res.json();
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
  requestId?: string;
};

let ttsStreamUnavailable = false;

export async function streamSynthesizeByJvs(
  text: string,
  voice: string | undefined,
  sessionId: string | undefined,
  kind: "content" | "cue",
  onPcmChunk: (chunk: ArrayBuffer, meta: { sampleRate: number; channels: number; elapsedMs?: number }) => Promise<void> | void,
): Promise<JvsTtsStreamResult> {
  if (ttsStreamUnavailable) {
    return { ok: false, unsupported: true, firstAudioMs: 0, totalMs: 0, chunks: 0, bytes: 0, sampleRate: 24000, channels: 1 };
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
    const ws = new WebSocket(url);
    const finish = (result: JvsTtsStreamResult) => {
      if (settled) return;
      settled = true;
      try {
        ws.close();
      } catch {
        // ignore
      }
      resolve(result);
    };
    ws.onopen = () => {
      ws.send(JSON.stringify({
        text,
        voice,
        session_id: sessionId,
        speed: DEFAULT_KOKORO_TTS_SPEED,
        kind,
      }));
    };
    ws.onerror = () => {
      if (settled) return;
      reject(new Error("JVS TTS stream websocket failed"));
    };
    ws.onmessage = async (ev) => {
      try {
        const msg = JSON.parse(String(ev.data || "{}"));
        if (msg.type === "meta") {
          sampleRate = Number(msg.sample_rate || sampleRate);
          channels = Number(msg.channels || channels);
          voiceChatTraceIfActive("tts.jvs_stream_meta", { sampleRate, channels, backend: msg.backend, model: msg.model, voice: msg.voice });
          return;
        }
        if (msg.type === "audio" && msg.audio_b64) {
          const chunk = base64ToArrayBuffer(String(msg.audio_b64));
          chunks += 1;
          bytes += chunk.byteLength;
          if (firstAudioMs <= 0) firstAudioMs = Date.now() - startedAt;
          await onPcmChunk(chunk, { sampleRate, channels, elapsedMs: Number(msg.elapsed_ms || 0) });
          return;
        }
        if (msg.type === "done") {
          requestId = String(msg.request_id || "");
          const totalMs = Date.now() - startedAt;
          voiceCompanionDebug("jvs.tts_stream_done", { chunks, bytes, firstAudioMs, totalMs, requestId });
          voiceChatTraceIfActive("tts.jvs_stream_done", { chunks, bytes, firstAudioMs, totalMs, sampleRate, channels, requestId });
          finish({ ok: true, firstAudioMs, totalMs, chunks, bytes, sampleRate, channels, requestId });
          return;
        }
        if (msg.type === "error") {
          const code = String(msg.code || "");
          const message = String(msg.message || "JVS TTS stream error");
          if (code === "stream_unsupported" || code === "tts_not_ready") {
            ttsStreamUnavailable = true;
            finish({ ok: false, unsupported: true, firstAudioMs, totalMs: Date.now() - startedAt, chunks, bytes, sampleRate, channels });
            return;
          }
          reject(new Error(message));
        }
      } catch (e) {
        reject(e);
      }
    };
    ws.onclose = () => {
      if (!settled && chunks > 0) {
        finish({ ok: true, firstAudioMs, totalMs: Date.now() - startedAt, chunks, bytes, sampleRate, channels, requestId });
      } else if (!settled) {
        reject(new Error("JVS TTS stream closed before audio"));
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
