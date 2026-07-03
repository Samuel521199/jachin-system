/**
 * 陪伴态 SSOT — state / ref / DOM 副作用 / Rust 事件同步。
 *
 * 改语音/TTS/路由时勿在此文件插入无关 useEffect。
 * 布局契约见 companionLayout.ts · 根因文档见 COMPANION_UI_REGRESSION_ROOT_CAUSE_ANALYSIS.md §14
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { invoke } from "@tauri-apps/api/core";
import {
  scheduleCompanionLayoutSyncWithRetry,
  resetCompanionLayoutSyncState,
} from "../components/Omni/companionLayoutCheck";
import { desktopDiagLog } from "../lib/desktopDiagLog";

type HideChatWindowResult = { companion: boolean; fullyHidden: boolean };

export interface UseCompanionModeResult {
  companionMode: boolean;
  setCompanionMode: React.Dispatch<React.SetStateAction<boolean>>;
  companionModeRef: React.MutableRefObject<boolean>;
  voiceCompanionActiveRef: React.MutableRefObject<boolean>;
  /** 唤醒 / 对账：确保 Rust 窗口 + React UI 都在陪伴态 */
  ensureCompanionSurfaceVisible: () => Promise<void>;
}

function applyCompanionDomSideEffects(companionMode: boolean): void {
  const root = document.getElementById("chat-root");
  if (!root) return;
  root.style.overflow = companionMode ? "visible" : "hidden";
  document.body.classList.toggle("companion-mode", companionMode);
  document.documentElement.classList.toggle("companion-mode", companionMode);
}

/** Rust SSOT：标志 + 尺寸 + dock + show（见 main.rs companion_restore_surface；不 emit，避免 React 反馈环） */
async function restoreCompanionSurfaceFromRust(): Promise<void> {
  await invoke("companion_restore_surface");
  await getCurrentWindow().show().catch(() => {});
  scheduleCompanionLayoutSyncWithRetry();
}

/** 同步失败时：只要 Rust 仍在陪伴态，就不把 React 降回 false（文档 §14.4 四份标志） */
async function reconcileCompanionModeAfterError(
  setCompanionMode: React.Dispatch<React.SetStateAction<boolean>>,
): Promise<boolean> {
  try {
    const rustCompanion = await invoke<boolean>("is_chat_companion_mode");
    if (rustCompanion) {
      setCompanionMode(true);
      await restoreCompanionSurfaceFromRust();
      return true;
    }
  } catch {
    // fall through
  }
  setCompanionMode(false);
  return false;
}

export function useCompanionMode(): UseCompanionModeResult {
  const [companionMode, setCompanionMode] = useState(false);
  const companionModeRef = useRef(companionMode);
  const voiceCompanionActiveRef = useRef(false);

  useEffect(() => {
    companionModeRef.current = companionMode;
  }, [companionMode]);

  const ensureCompanionSurfaceVisible = useCallback(async () => {
    setCompanionMode(true);
    try {
      const rustCompanion = await invoke<boolean>("is_chat_companion_mode");
      if (!rustCompanion) {
        const r = await invoke<HideChatWindowResult>("hide_chat_window");
        if (!r?.companion) {
          throw new Error("hide_chat_window did not enter companion mode");
        }
      }
      await restoreCompanionSurfaceFromRust();
      setCompanionMode(true);
      void desktopDiagLog("react_companion_surface_ok", { source: "ensureCompanionSurfaceVisible" });
    } catch (e) {
      void desktopDiagLog("react_companion_surface_err", { err: String(e) });
      await reconcileCompanionModeAfterError(setCompanionMode);
    }
  }, []);

  // ════════════════════════════════════════════════════════════════════════════
  // COMPANION MODE EFFECTS — 勿在此区之前插入新 useEffect
  // 顺序敏感：DOM class → Rust 恢复 → layout sync
  // 改动前读 docs/COMPANION_UI_REGRESSION_ROOT_CAUSE_ANALYSIS.md §14.8–14.10
  // ════════════════════════════════════════════════════════════════════════════

  /** 仅负责 DOM / overflow / companion-mode class（勿在 cleanup 里误清陪伴态） */
  useEffect(() => {
    applyCompanionDomSideEffects(companionMode);
  }, [companionMode]);

  /** 陪伴态 React state 每次变化时打一条，与 Rust emit / 窗口 API 对照 */
  useEffect(() => {
    void (async () => {
      try {
        const w = getCurrentWindow();
        const [min, vis] = await Promise.all([w.isMinimized(), w.isVisible()]);
        void desktopDiagLog("react_companion_mode_state", {
          companionModeReact: companionMode,
          label: w.label,
          minimized: min,
          visible: vis,
        });
      } catch (e) {
        void desktopDiagLog("react_companion_mode_state_err", { err: String(e) });
      }
    })();
  }, [companionMode]);

  /** 进入陪伴态：延迟 Rust 恢复，让 hide_chat_window 先完成缩窗（restore 不 emit，无反馈环） */
  useEffect(() => {
    if (!companionMode) return;

    resetCompanionLayoutSyncState();
    let cancelled = false;
    const timer = setTimeout(() => {
      void (async () => {
        if (cancelled) return;
        try {
          await restoreCompanionSurfaceFromRust();
        } catch (e) {
          if (cancelled) return;
          void desktopDiagLog("react_companion_sync_err", { err: String(e) });
          await reconcileCompanionModeAfterError(setCompanionMode);
        }
      })();
    }, 80);

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [companionMode]);

  /** Rust ↔ React 定期对账：窗口获焦时若 Rust 已在陪伴态则拉回 UI */
  useEffect(() => {
    let unlisten: (() => void) | undefined;
    void getCurrentWindow()
      .onFocusChanged(({ payload: focused }) => {
        if (!focused) return;
        void invoke<boolean>("is_chat_companion_mode")
          .then((rustCompanion) => {
        if (!rustCompanion) return;
        setCompanionMode(true);
        void restoreCompanionSurfaceFromRust();
          })
          .catch(() => {});
      })
      .then((fn) => {
        unlisten = fn;
      })
      .catch(() => {});
    return () => {
      unlisten?.();
    };
  }, []);

  /**
   * 启动时同步 Rust 陪伴态。若该 invoke 较慢，用户可能已先按 Esc 坍缩；
   * 后到的 false 会错误盖掉 true → UI 与大窗不同步、球「随机」才出现。
   * 规则：本地已是陪伴态时，不再被这次启动同步降成 false。
   */
  useEffect(() => {
    let cancelled = false;
    void invoke<boolean>("is_chat_companion_mode")
      .then((v) => {
        if (cancelled) return;
        void desktopDiagLog("react_startup_companion_sync", {
          rustCompanionReported: v,
          note: "before merge with optimistic local state",
        });
        setCompanionMode((prev) => (prev ? prev : Boolean(v)));
      })
      .catch((e) => {
        void desktopDiagLog("react_startup_companion_sync_err", { err: String(e) });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  /** Rust → React：Esc / 唤醒 / peek 等路径 emit omni-companion-mode（勿在此再调 restore，避免循环） */
  useEffect(() => {
    let unlisten: (() => void) | undefined;
    listen<{ companion: boolean }>("omni-companion-mode", (ev) => {
      const next = Boolean(ev.payload?.companion);
      setCompanionMode(next);
      if (next) {
        resetCompanionLayoutSyncState();
        scheduleCompanionLayoutSyncWithRetry();
      }
      void (async () => {
        try {
          const w = getCurrentWindow();
          const [min, vis, focused] = await Promise.all([
            w.isMinimized(),
            w.isVisible(),
            w.isFocused().catch(() => false),
          ]);
          void desktopDiagLog("react_omni_companion_event", {
            payloadCompanion: next,
            label: w.label,
            minimized: min,
            visible: vis,
            focused,
          });
        } catch (e) {
          void desktopDiagLog("react_omni_companion_event_err", { err: String(e) });
        }
      })();
    })
      .then((fn) => {
        unlisten = fn;
      })
      .catch((err) => {
        console.warn("[Omni] listen omni-companion-mode failed:", err);
      });
    return () => {
      unlisten?.();
    };
  }, []);

  // ════════════════════════════════════════════════════════════════════════════
  // END COMPANION MODE EFFECTS
  // ════════════════════════════════════════════════════════════════════════════

  return {
    companionMode,
    setCompanionMode,
    companionModeRef,
    voiceCompanionActiveRef,
    ensureCompanionSurfaceVisible,
  };
}
