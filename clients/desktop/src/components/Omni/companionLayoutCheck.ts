import { invoke } from "@tauri-apps/api/core";
import {
  computeCompanionLogicalHeight,
  GLOW_OVERFLOW_PX,
  MIN_WINDOW_LOGICAL,
} from "./companionLayout";

const LOG_PREFIX = "[CompanionLayout]";

/** 补窗时在实测高度上额外加的逻辑像素余量（glow + 字体行高误差） */
const LAYOUT_HEIGHT_BUFFER_PX = 12;

let layoutSyncTimer: ReturnType<typeof setTimeout> | null = null;
let layoutSyncInProgress = false;
let lastSyncedLogicalHeight = 0;

/** 程序化 resize 期间禁止 OrbWindow onMoved 写 dock，避免 set_size ↔ set_position 反馈环 */
export function isCompanionLayoutSyncInProgress(): boolean {
  return layoutSyncInProgress;
}

/** 每次进入陪伴态时重置，确保首帧会 invoke 补窗 */
export function resetCompanionLayoutSyncState(): void {
  lastSyncedLogicalHeight = 0;
  layoutSyncInProgress = false;
  if (layoutSyncTimer) {
    clearTimeout(layoutSyncTimer);
    layoutSyncTimer = null;
  }
}

/**
 * 实测陪伴 UI 内容高度（勿用 scrollHeight：h-full 链会把整窗高度误当作内容高）。
 */
export function measureCompanionContentHeight(rootEl: HTMLElement): number {
  const rootTop = rootEl.getBoundingClientRect().top;
  let maxBottom = 0;

  const orbWrap = rootEl.querySelector("[data-companion-orb]");
  if (orbWrap) {
    maxBottom = Math.max(
      maxBottom,
      orbWrap.getBoundingClientRect().bottom - rootTop + GLOW_OVERFLOW_PX,
    );
  }

  const voiceBtn = rootEl.querySelector("[data-companion-voice-btn]");
  if (voiceBtn) {
    maxBottom = Math.max(maxBottom, voiceBtn.getBoundingClientRect().bottom - rootTop);
  } else {
    const stateEl = rootEl.querySelector("[data-companion-state]");
    if (stateEl) {
      maxBottom = Math.max(maxBottom, stateEl.getBoundingClientRect().bottom - rootTop);
    }
  }

  if (maxBottom > 0) {
    return Math.ceil(maxBottom + 12);
  }

  return MIN_WINDOW_LOGICAL;
}

/**
 * 按实测内容高度同步 Rust 陪伴窗尺寸（文档 §13 解法 2-D / 3-C）。
 */
export async function syncCompanionWindowSize(rootEl: HTMLElement | null): Promise<void> {
  if (!rootEl) return;
  const contentH = measureCompanionContentHeight(rootEl);
  const logical = computeCompanionLogicalHeight(contentH + LAYOUT_HEIGHT_BUFFER_PX);
  if (Math.abs(logical - lastSyncedLogicalHeight) < 4) {
    return;
  }
  layoutSyncInProgress = true;
  try {
    await invoke("ensure_companion_window_size", { contentLogicalHeight: logical });
    lastSyncedLogicalHeight = logical;
  } catch (e) {
    console.warn(`${LOG_PREFIX} ensure_companion_window_size failed:`, e);
  } finally {
    layoutSyncInProgress = false;
  }
}

/**
 * 挂载/更新后测量陪伴 UI 是否被 native 窗口裁切；若溢出则 invoke 补高并打可检索日志。
 */
export async function checkCompanionLayout(rootEl: HTMLElement | null): Promise<void> {
  if (!rootEl) return;

  const contentH = measureCompanionContentHeight(rootEl);
  const windowH = window.innerHeight;
  let needsResize = false;

  if (contentH > windowH) {
    const delta = contentH - windowH;
    console.warn(
      `${LOG_PREFIX} WARN: content clipped content_h=${contentH} window_h=${windowH} delta=${delta}`,
    );
    needsResize = true;
  }

  const voiceBtn = rootEl.querySelector("[data-companion-voice-btn]");
  const stateEl = rootEl.querySelector("[data-companion-state]");
  const orbWrap = rootEl.querySelector("[data-companion-orb]");
  if (orbWrap) {
    const rect = orbWrap.getBoundingClientRect();
    if (rect.bottom + GLOW_OVERFLOW_PX > windowH + 1 || rect.top < 0) {
      console.warn(
        `${LOG_PREFIX} WARN: orb out of viewport top=${rect.top.toFixed(0)} bottom=${rect.bottom.toFixed(0)} window_h=${windowH}`,
      );
      needsResize = true;
    }
  }
  if (voiceBtn) {
    const rect = voiceBtn.getBoundingClientRect();
    if (rect.bottom > windowH + 1 || rect.top < 0) {
      console.warn(
        `${LOG_PREFIX} WARN: voice button out of viewport top=${rect.top.toFixed(0)} bottom=${rect.bottom.toFixed(0)} window_h=${windowH}`,
      );
      needsResize = true;
    }
  }
  if (stateEl) {
    const rect = stateEl.getBoundingClientRect();
    if (rect.bottom > windowH + 1 || rect.top < 0) {
      console.warn(
        `${LOG_PREFIX} WARN: state text out of viewport top=${rect.top.toFixed(0)} bottom=${rect.bottom.toFixed(0)} window_h=${windowH}`,
      );
      needsResize = true;
    }
  }

  if (needsResize) {
    await syncCompanionWindowSize(rootEl);
  }
}

/** 双 rAF + 防抖：先补窗到 baseline，再检测裁切（不 shrink，避免振荡） */
export function scheduleCompanionLayoutSync(rootEl: HTMLElement | null): void {
  if (layoutSyncTimer) clearTimeout(layoutSyncTimer);
  layoutSyncTimer = setTimeout(() => {
    layoutSyncTimer = null;
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        void (async () => {
          await syncCompanionWindowSize(rootEl);
          await checkCompanionLayout(rootEl);
        })();
      });
    });
  }, 280);
}

/** 陪伴 UI 挂载可能晚于 companionMode effect，重试直到找到 [data-companion-root] */
export function scheduleCompanionLayoutSyncWithRetry(maxAttempts = 12): void {
  let attempts = 0;
  const tick = () => {
    const rootEl = document.querySelector("[data-companion-root]") as HTMLElement | null;
    if (rootEl) {
      scheduleCompanionLayoutSync(rootEl);
      return;
    }
    attempts += 1;
    if (attempts < maxAttempts) {
      requestAnimationFrame(tick);
    } else {
      console.warn(`${LOG_PREFIX} WARN: data-companion-root not found after ${maxAttempts} frames`);
    }
  };
  requestAnimationFrame(tick);
}
