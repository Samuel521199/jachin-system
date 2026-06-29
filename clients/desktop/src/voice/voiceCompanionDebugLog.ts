/**
 * 陪伴语音调试日志 → %USERPROFILE%\.jachin\jachin_debug\voice_companion.log（Rust 落盘）
 */
import { invoke } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";

let webviewLabel = "unknown";

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
  const message = truncVoiceLog(String(payload.msg ?? stage), 240);
  const detail = JSON.stringify({
    ts: Date.now(),
    webview: webviewLabel,
    ...payload,
  });
  console.debug(`[voice_companion][${stage}]`, payload);
  void invoke("voice_companion_debug_log", {
    webview: webviewLabel,
    stage,
    message,
    detail,
  }).catch(() => {
    /* 浏览器预览 */
  });
}
