import { invoke } from "@tauri-apps/api/core";
import { emit, emitTo } from "@tauri-apps/api/event";
import type { SensoryAnswerMeta, SensoryChunkMeta } from "../hooks/useSensoryWebSocket";
import { stripAssistantUiProtocol } from "../components/Chat/pendingConfirmationProtocol";

// chat -> unified Omni event bridge. The old standalone HUD window has been retired.
export const VOICE_COMPANION_L3_EVENT = "voice-companion-l3";

// chat -> unified Omni event bridge.
export const VOICE_COMPANION_USER_EVENT = "voice-companion-user";

// voice / quick input -> chat: request one canonical L3 send.
export const VOICE_COMPANION_SEND_EVENT = "voice-companion-send";

// assistant voice replay into the existing TTS orchestrator only.
export const VOICE_COMPANION_TTS_EVENT = "voice-companion-tts";

export type VoiceCompanionL3Payload = {
  kind: "thinking" | "chunk" | "answer";
  delta?: string;
  content?: string;
  runId?: string;
  meta?: SensoryAnswerMeta;
  chunkMeta?: SensoryChunkMeta;
};

export type VoiceCompanionTtsPayload = VoiceCompanionL3Payload;

async function emitToOmniSurface(event: string, payload: Record<string, unknown>): Promise<void> {
  try {
    await invoke("voice_companion_emit_to_omni", { event, payload });
    return;
  } catch {
    await emit(event, payload);
  }
}

export async function emitCompanionL3ToHud(payload: VoiceCompanionL3Payload): Promise<void> {
  await emitToOmniSurface(VOICE_COMPANION_L3_EVENT, {
    ...payload,
    delta: payload.delta ? stripAssistantUiProtocol(payload.delta) : payload.delta,
    content: payload.content ? stripAssistantUiProtocol(payload.content) : payload.content,
  } as Record<string, unknown>);
}

export async function emitCompanionTtsToChat(payload: VoiceCompanionTtsPayload): Promise<void> {
  const safePayload = {
    ...payload,
    delta: payload.delta ? stripAssistantUiProtocol(payload.delta) : payload.delta,
    content: payload.content ? stripAssistantUiProtocol(payload.content) : payload.content,
  } as Record<string, unknown>;
  try {
    await emitTo("chat", VOICE_COMPANION_TTS_EVENT, safePayload);
  } catch {
    await emit(VOICE_COMPANION_TTS_EVENT, safePayload);
  }
}

export async function emitCompanionUserToHud(content: string): Promise<void> {
  const t = content.trim();
  if (!t) return;
  await emitToOmniSurface(VOICE_COMPANION_USER_EVENT, { content: t });
}

export async function armCompanionVoiceSession(): Promise<void> {
  await emit("hud-voice-session", { active: true });
}

export async function requestCompanionL3Send(content: string): Promise<void> {
  const t = content.trim();
  if (!t) return;
  try {
    await emitTo("chat", VOICE_COMPANION_SEND_EVENT, { content: t });
  } catch {
    await emit("voice-sim-user-input", { content: t });
  }
}
