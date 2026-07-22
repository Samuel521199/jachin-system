/**
 * Unified Omni surface state.
 *
 * The retired companion/HUD UI no longer owns a separate page or mini window.
 * Voice, text, wake events and confirmations all share the Omni chat surface.
 * This hook keeps the previous public shape for compatibility, while forcing
 * every visibility request back to the main Omni window.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { desktopDiagLog } from "../lib/desktopDiagLog";

export interface UseCompanionModeResult {
  companionMode: boolean;
  setCompanionMode: React.Dispatch<React.SetStateAction<boolean>>;
  companionModeRef: React.MutableRefObject<boolean>;
  voiceCompanionActiveRef: React.MutableRefObject<boolean>;
  ensureCompanionSurfaceVisible: () => Promise<void>;
}

function clearRetiredCompanionDom(): void {
  const root = document.getElementById("chat-root");
  if (root) {
    root.style.overflow = "hidden";
  }
  document.body.classList.remove("companion-mode");
  document.documentElement.classList.remove("companion-mode");
}

export function useCompanionMode(): UseCompanionModeResult {
  const [companionMode, setCompanionModeState] = useState(false);
  const companionModeRef = useRef(false);
  const voiceCompanionActiveRef = useRef(false);

  const setCompanionMode = useCallback<React.Dispatch<React.SetStateAction<boolean>>>((value) => {
    const requested = typeof value === "function" ? value(false) : value;
    if (requested) {
      void desktopDiagLog("react_companion_mode_ignored", {
        requested: true,
        reason: "unified_omni_only",
      });
    }
    companionModeRef.current = false;
    setCompanionModeState(false);
    clearRetiredCompanionDom();
  }, []);

  const ensureCompanionSurfaceVisible = useCallback(async () => {
    companionModeRef.current = false;
    setCompanionModeState(false);
    clearRetiredCompanionDom();
    const w = getCurrentWindow();
    await w.show().catch(() => {});
    await w.unminimize().catch(() => {});
    await w.setFocus().catch(() => {});
    void desktopDiagLog("react_unified_omni_surface_ok", { source: "ensureCompanionSurfaceVisible" });
  }, []);

  useEffect(() => {
    companionModeRef.current = false;
    if (companionMode) {
      setCompanionModeState(false);
    }
    clearRetiredCompanionDom();
  }, [companionMode]);

  useEffect(() => {
    clearRetiredCompanionDom();
  }, []);

  return {
    companionMode: false,
    setCompanionMode,
    companionModeRef,
    voiceCompanionActiveRef,
    ensureCompanionSurfaceVisible,
  };
}
