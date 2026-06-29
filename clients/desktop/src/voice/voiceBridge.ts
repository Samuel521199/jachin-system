import { invoke } from "@tauri-apps/api/core";
import { truncVoiceLog, voiceCompanionDebug } from "./voiceCompanionDebugLog";
import { voiceChatTraceIfActive } from "./voiceChatTraceLog";

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
  confidence: number;
  duration_ms: number;
  language: string;
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

export async function synthesizeByJvs(text: string, voice?: string, sessionId?: string): Promise<Blob> {
  const url = `${JVS_BASE}/v1/tts/synthesize`;
  const startedAt = Date.now();
  voiceCompanionDebug("jvs.tts_fetch_start", {
    url,
    text: truncVoiceLog(text, 120),
    sessionId,
    voice,
  });
  voiceChatTraceIfActive("tts.jvs_fetch_start", {
    url,
    text: truncVoiceLog(text, 160),
    textLen: text.length,
    sessionId,
    voice,
  });
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      text,
      voice,
      session_id: sessionId,
    }),
  });
  const responseMs = Date.now() - startedAt;
  const audioDurationMs = Number(res.headers.get("X-Jachin-Duration-Ms") || 0);
  const serverSynthMs = Number(res.headers.get("X-Jachin-TTS-Synth-Ms") || 0);
  const attempts = Number(res.headers.get("X-Jachin-TTS-Attempts") || 0);
  const maxNewFrames = Number(res.headers.get("X-Jachin-TTS-Max-New-Frames") || 0);
  const quality = res.headers.get("X-Jachin-TTS-Quality") || "";
  voiceChatTraceIfActive("tts.jvs_fetch_response", {
    status: res.status,
    ok: res.ok,
    latencyMs: responseMs,
    serverSynthMs,
    audioDurationMs,
    attempts,
    maxNewFrames,
    quality,
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
    sessionId,
    voice,
  });
  return blob;
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


