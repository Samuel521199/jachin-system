/**
 * 大窗语音按钮全链路追踪 -> %USERPROFILE%\.jachin\jachin_debug\voice_chat.log
 *
 * 阶段（stage）概览：
 * turn.begin | ptt.start | ptt.stop | stt.audio_ready | stt.jvs_* | l3.send | l3.chunk | l3.step | l3.answer | tts.* | turn.end
 */
import { invoke } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";
import type { VoiceUxProfile } from "./voiceProfiles";
import {
  beginVoiceTurnDiagnostics,
  endVoiceTurnDiagnostics,
  recordVoiceTurnDiagnosticEvent,
  snapshotVoiceTurnDiagnostics,
} from "./voiceTurnDiagnostics";

export type VoiceChatUiState = {
  machineState?: string;
  recordingStatus?: string;
  isRecording?: boolean;
  isVadActive?: boolean;
  isLoading?: boolean;
  isTyping?: boolean;
  ttsPlaying?: boolean;
  ttsEnabled?: boolean;
  sensoryConnected?: boolean;
  l2Available?: boolean;
  companionMode?: boolean;
  sessionId?: string | null;
};

type ActiveTrace = {
  id: string;
  profile: VoiceUxProfile;
  startedAt: number;
  lastAt: number;
  webview: string;
};

let webviewLabel = "chat";
let activeTrace: ActiveTrace | null = null;
const lastTraceStageAt = new Map<string, number>();
const TRACE_IMPORTANT_STAGE_RE =
  /(turn\.|stt\.recognized|stt\.fail|stt\.jvs_.*fail|stt\.jvs_.*ok|l3\.send_start|l3\.answer|l3\.timeout|l3\.error|sv\.|guard|task|confirm|cancel|interrupt|replan|tts_first|slow|severe|fail|warn)/i;
const TRACE_NOISY_STAGE_RE =
  /(chunk|pcm_chunk|playback_.*chunk|orchestrator\.chunk|coalesce|dequeue|heartbeat|audio_level|meter|stream_idle)/i;

function verboseVoiceTraceEnabled(): boolean {
  try {
    if (localStorage.getItem("jachin.voice.verboseTraceLog") === "1") return true;
  } catch {
    // ignore
  }
  return String(import.meta.env.VITE_JACHIN_VOICE_VERBOSE_TRACE || "").trim() === "1";
}

function shouldWriteVoiceTrace(stage: string): boolean {
  if (verboseVoiceTraceEnabled()) return true;
  if (TRACE_NOISY_STAGE_RE.test(stage) && !TRACE_IMPORTANT_STAGE_RE.test(stage)) return false;
  const now = Date.now();
  const minStageMs = TRACE_IMPORTANT_STAGE_RE.test(stage) ? 120 : 1_000;
  const prev = lastTraceStageAt.get(stage) || 0;
  if (now - prev < minStageMs) return false;
  lastTraceStageAt.set(stage, now);
  return true;
}

export function truncChatTrace(text: string, max = 320): string {
  const t = text.replace(/\s+/g, " ").trim();
  if (t.length <= max) return t;
  return `${t.slice(0, max)}...(${t.length})`;
}

export async function initVoiceChatTraceLog(): Promise<void> {
  try {
    webviewLabel = getCurrentWindow().label;
  } catch {
    webviewLabel = "chat";
  }
  voiceChatTrace("init", { webview: webviewLabel });
}

export function getActiveVoiceChatTraceId(): string | null {
  return activeTrace?.id ?? null;
}

export function getActiveVoiceChatProfile(): VoiceUxProfile | null {
  return activeTrace?.profile ?? null;
}

function newTraceId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID().slice(0, 8);
  }
  return `v${Date.now().toString(36)}`;
}

/** 新一轮语音交互（PTT 按下或 VAD 截句） */
export function beginVoiceChatTrace(profile: VoiceUxProfile, ui?: VoiceChatUiState): string {
  const id = newTraceId();
  const now = Date.now();
  activeTrace = { id, profile, startedAt: now, lastAt: now, webview: webviewLabel };
  beginVoiceTurnDiagnostics(id, profile);
  voiceChatTrace("turn.begin", {
    profile,
    ui,
    msg: `profile=${profile}`,
  });
  return id;
}

/** 结束追踪（成功 / 失败 / 取消） */
export function endVoiceChatTrace(
  outcome:
    | "ok"
    | "stt_fail"
    | "send_fail"
    | "l3_error"
    | "timeout"
    | "cancel"
    | "ptt_fail"
    | "clarification_required",
  extra: Record<string, unknown> = {},
): void {
  if (!activeTrace) {
    voiceChatTrace("turn.end_orphan", { outcome, ...extra });
    return;
  }
  const elapsedMs = Date.now() - activeTrace.startedAt;
  voiceChatTrace("turn.end", {
    outcome,
    elapsedMs,
    profile: activeTrace.profile,
    ...extra,
    msg: `${outcome} ${elapsedMs}ms`,
  });
  endVoiceTurnDiagnostics(outcome, extra);
  activeTrace = null;
}

export function voiceChatTrace(
  stage: string,
  payload: Record<string, unknown> = {},
): void {
  if (!shouldWriteVoiceTrace(stage)) return;
  const traceId = (payload.traceId as string | undefined) ?? activeTrace?.id ?? "none";
  const now = Date.now();
  const elapsedMs = activeTrace ? now - activeTrace.startedAt : undefined;
  const sincePrevMs = activeTrace ? now - activeTrace.lastAt : undefined;
  const profile = activeTrace?.profile;
  const iso = new Date().toISOString();
  const message = truncChatTrace(String(payload.msg ?? stage), 400);
  const detail = JSON.stringify({
    iso,
    elapsedMs,
    sincePrevMs,
    profile,
    webview: webviewLabel,
    ...payload,
  });
  if (activeTrace) {
    activeTrace.lastAt = now;
  }
  recordVoiceTurnDiagnosticEvent(stage, payload, elapsedMs, sincePrevMs);
  if (verboseVoiceTraceEnabled() || TRACE_IMPORTANT_STAGE_RE.test(stage)) {
    console.debug(`[voice_chat][${traceId}][${stage}]`, payload);
  }
  void invoke("voice_chat_trace_log", {
    traceId,
    stage,
    message,
    detail,
  }).catch(() => {
    /* 浏览器预览 */
  });
}

/** 若当前有语音 trace，则写入（避免污染普通打字发送） */
export function voiceChatTraceIfActive(stage: string, payload: Record<string, unknown> = {}): void {
  if (!activeTrace) return;
  voiceChatTrace(stage, payload);
}

export function getVoiceTurnDiagnosticsSnapshot(): ReturnType<typeof snapshotVoiceTurnDiagnostics> {
  return snapshotVoiceTurnDiagnostics();
}
