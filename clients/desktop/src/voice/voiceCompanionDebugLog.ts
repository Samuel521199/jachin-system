/**
 * 陪伴语音调试日志 → %USERPROFILE%\.jachin\jachin_debug\voice_companion.log（Rust 落盘）
 */
import { invoke } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";

let webviewLabel = "unknown";
let lastDebugAt = 0;
const lastStageAt = new Map<string, number>();
const IMPORTANT_STAGE_RE =
  /(error|fail|warn|reject|recognized|send_start|answer|guard|owner_track|barge|start|stop|toggle|task|interrupt|replan|route|decision|confirm|cancel)/i;
const NOISY_STAGE_RE =
  /(chunk|pcm|play_loop|play_start|play_ok|play_ended|cache_hit|phase|level|meter|tick|heartbeat|stream_idle)/i;

function verboseVoiceDebugEnabled(): boolean {
  try {
    if (localStorage.getItem("jachin.voice.verboseDebugLog") === "1") return true;
  } catch {
    // ignore
  }
  return String(import.meta.env.VITE_JACHIN_VOICE_VERBOSE_LOG || "").trim() === "1";
}

function shouldWriteCompanionDebug(stage: string): boolean {
  if (verboseVoiceDebugEnabled()) return true;
  if (NOISY_STAGE_RE.test(stage) && !IMPORTANT_STAGE_RE.test(stage)) return false;
  const now = Date.now();
  const minGlobalMs = IMPORTANT_STAGE_RE.test(stage) ? 80 : 750;
  if (now - lastDebugAt < minGlobalMs) return false;
  const minStageMs = IMPORTANT_STAGE_RE.test(stage) ? 250 : 2_000;
  const prev = lastStageAt.get(stage) || 0;
  if (now - prev < minStageMs) return false;
  lastDebugAt = now;
  lastStageAt.set(stage, now);
  return true;
}

export function truncVoiceLog(text: string, max = 160): string {
  const t = text.replace(/\s+/g, " ").trim();
  if (t.length <= max) return t;
  return `${t.slice(0, max)}…(${t.length})`;
}

export async function initVoiceCompanionDebugLog(): Promise<void> {
  try {
    webviewLabel = getCurrentWindow().label;
    voiceCompanionDebug("init", { label: webviewLabel });
  } catch {
    voiceCompanionDebug("init", { label: webviewLabel, note: "non-tauri" });
  }
}

export function voiceCompanionDebug(
  stage: string,
  payload: Record<string, unknown> = {},
): void {
  if (!shouldWriteCompanionDebug(stage)) return;
  const message = truncVoiceLog(String(payload.msg ?? stage), 240);
  const detail = JSON.stringify({
    ts: Date.now(),
    webview: webviewLabel,
    ...payload,
  });
  if (verboseVoiceDebugEnabled() || IMPORTANT_STAGE_RE.test(stage)) {
    console.debug(`[voice_companion][${stage}]`, payload);
  }
  void invoke("voice_companion_debug_log", {
    webview: webviewLabel,
    stage,
    message,
    detail,
  }).catch(() => {
    /* 浏览器预览 */
  });
}
