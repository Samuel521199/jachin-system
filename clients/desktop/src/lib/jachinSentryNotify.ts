/**
 * Jachin 哨兵：Omni 最小化 / 陪伴圆 / 隐藏时右下角自定义 toast（Tauri 透明子窗口）。
 * 非 Tauri 或 invoke 失败时静默跳过。
 */
import { invoke } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";

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
    await invoke("jachin_sentry_notify", { title, body });
  } catch {
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
    if (w.label !== "chat") return false;
    const min = await w.isMinimized();
    if (min) return true;
    const vis = await w.isVisible();
    return !vis;
  } catch {
    return false;
  }
}

/**
 * 陪伴圆 / 最小化 / 不可见时提醒；展开大窗时不打扰。
 */
export async function maybeNotifyJachinAssistantDone(
  companionMode: boolean,
  summary: string,
  variant: SentryNotifyVariant = "answer",
): Promise<void> {
  const surface = companionMode || (await windowSuggestsBackground());
  if (!surface) return;
  await sendJachinNotification(TITLE[variant], summarizeForSentryNotify(summary));
}

/** 闹钟 / 定时器：到点弹哨兵（不判断最小化，由调用方决定何时调用） */
export function scheduleJachinAlarm(delayMs: number, title: string, message: string): () => void {
  const id = window.setTimeout(() => {
    void sendJachinNotification(title, message);
  }, Math.max(0, delayMs));
  return () => clearTimeout(id);
}
