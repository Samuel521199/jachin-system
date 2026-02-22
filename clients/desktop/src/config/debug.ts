/**
 * 调试开关集中管理
 * 只改下面「手动开关」即可统一控制 Chat / 拖拽调试日志。
 */

const isDev = typeof import.meta !== "undefined" && (import.meta as { env?: { DEV?: boolean } }).env?.DEV === true;

/** 手动开关：true=强制开启，false=不打印，undefined=跟随环境（dev 开 / build 关） */
const CHAT_DRAG_DEBUG_OVERRIDE: boolean | undefined = true;

export const CHAT_DRAG_DEBUG: boolean = CHAT_DRAG_DEBUG_OVERRIDE ?? isDev;

export function chatDragLog(scope: string, ...args: unknown[]) {
  if (CHAT_DRAG_DEBUG) console.log(`[${scope}]`, ...args);
}
