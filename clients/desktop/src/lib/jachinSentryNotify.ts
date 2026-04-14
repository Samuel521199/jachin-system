/**
 * Jachin 哨兵：在「用户未盯着完整 Omni 条」时右下角 toast（Tauri 子窗口）。
 * - 窗口 API：`isMinimized` / `isVisible`（须 capabilities 放行，见 default.json）。
 * - 陪伴圆：`is_chat_companion_mode`（Rust 原子，与 Esc 坍缩一致；非前端 state）。
 */
import { invoke } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { desktopDiagLog } from "./desktopDiagLog";

export type SentryNotifyVariant = "answer" | "rejected" | "error" | "l2";

const TITLE: Record<SentryNotifyVariant, string> = {
  answer: "Jachin · 回复完成",
  l2: "Jachin · 回复完成",
  rejected: "Jachin · 已拒绝",
  error: "Jachin · 出错",
};

/** 任意前端（优先 chat 窗口）调用：弹出哨兵条 */
export async function sendJachinNotification(title: string, message: string): Promise<void> {
  const body = message.trim().slice(0, 220);
  try {
    await desktopDiagLog("sentry_send_start", {
      titleLen: title.length,
      bodyLen: body.length,
      titlePreview: title.slice(0, 80),
    });
    await invoke("jachin_sentry_notify", { title, body });
    await desktopDiagLog("sentry_send_invoke_ok", { titleLen: title.length });
  } catch (e) {
    await desktopDiagLog("sentry_send_invoke_err", { err: String(e) });
    /* 浏览器预览或非 Tauri */
  }
}

/** 摘要：去换行、截断 */
export function summarizeForSentryNotify(text: string, maxLen = 120): string {
  const t = text.replace(/\s+/g, " ").trim();
  if (t.length <= maxLen) return t || "（无摘要）";
  return `${t.slice(0, maxLen)}…`;
}

async function windowSuggestsBackground(): Promise<boolean> {
  try {
    const w = getCurrentWindow();
    const label = w.label;
    if (label !== "chat") {
      await desktopDiagLog("sentry_window_gating", {
        step: "skip_wrong_label",
        label,
        suggestBackground: false,
      });
      return false;
    }
    const min = await w.isMinimized();
    if (min) {
      await desktopDiagLog("sentry_window_gating", {
        step: "minimized_true",
        label,
        minimized: true,
        suggestBackground: true,
      });
      return true;
    }
    const vis = await w.isVisible();
    const suggest = !vis;
    await desktopDiagLog("sentry_window_gating", {
      step: "visibility_check",
      label,
      minimized: min,
      visible: vis,
      suggestBackground: suggest,
    });
    return suggest;
  } catch (e) {
    await desktopDiagLog("sentry_window_gating_err", { err: String(e) });
    return false;
  }
}

/**
 * 最小化到任务栏、窗口不可见、或已进入右下角陪伴圆时提醒；完整大窗前台不打扰。
 */
export async function maybeNotifyJachinAssistantDone(
  summary: string,
  variant: SentryNotifyVariant = "answer",
): Promise<void> {
  const [bg, rustCompanion] = await Promise.all([
    windowSuggestsBackground(),
    invoke<boolean>("is_chat_companion_mode").catch(() => false),
  ]);
  const allow = bg || rustCompanion;
  if (!allow) {
    await desktopDiagLog("sentry_maybe_notify", {
      action: "skip_not_background",
      variant,
      summaryLen: summary.length,
      windowSuggestsBackground: bg,
      rustCompanion,
    });
    return;
  }
  await desktopDiagLog("sentry_maybe_notify", {
    action: "will_send_notification",
    variant,
    summaryLen: summary.length,
    title: TITLE[variant],
    windowSuggestsBackground: bg,
    rustCompanion,
  });
  await sendJachinNotification(TITLE[variant], summarizeForSentryNotify(summary));
}

/** 闹钟 / 定时器：到点弹哨兵（不判断最小化，由调用方决定何时调用） */
export function scheduleJachinAlarm(delayMs: number, title: string, message: string): () => void {
  const id = window.setTimeout(() => {
    void sendJachinNotification(title, message);
  }, Math.max(0, delayMs));
  return () => clearTimeout(id);
}
