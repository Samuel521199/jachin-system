/**
 * Skill 画布打开时扩 Omni 窗口：走 Tauri 主线程命令；最小总宽由 Rust 按 **scale_factor** 换算（`CHAT_SKILL_CANVAS_MIN_TOTAL_LOGICAL`）。
 * 关画布时前端不再自动 `restore`，以免左栏 flex 拉满；需要缩窗可显式调用 `restoreChatWindowAfterSkillCanvas`。
 */

import { invoke } from "@tauri-apps/api/core";

/** OMNI 激活画布时左侧聊天列固定宽度（与 chat.tsx 中 style 一致） */
export const SKILL_CHAT_COLUMN_WIDTH = 450;

/** 与 Rust `CHAT_SKILL_CANVAS_MIN_TOTAL_WIDTH` 扩窗逻辑对应的逻辑像素增量（文档/注释用） */
export const SKILL_CANVAS_WINDOW_EXPAND_LOGICAL = 480;

/** @deprecated 与 Rust 侧最小总宽 930 对齐 */
export const SKILL_CANVAS_PANEL_WIDTH_LOGICAL = SKILL_CANVAS_WINDOW_EXPAND_LOGICAL;

export async function expandChatWindowForSkillCanvas(): Promise<void> {
  try {
    await invoke("expand_chat_window_for_skill_canvas");
  } catch {
    /* 浏览器预览或非 Tauri */
  }
}

export async function restoreChatWindowAfterSkillCanvas(): Promise<void> {
  try {
    await invoke("restore_chat_window_after_skill_canvas_rust");
  } catch {
    /* noop */
  }
}
