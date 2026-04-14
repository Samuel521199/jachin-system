import { useCallback, useEffect, useState } from "react";
import {
  DESKTOP_UI_LANG_EVENT,
  DESKTOP_UI_LANG_KEY,
  readDesktopUiLang,
  writeDesktopUiLang,
  type DesktopUiLang,
} from "../utils/desktopUiLang";

function applyLangToStorage(next: DesktopUiLang): void {
  try {
    window.localStorage.setItem(DESKTOP_UI_LANG_KEY, next);
  } catch {
    /* noop */
  }
}

/**
 * 桌面端（控制台 Horizon + Omni）共用语言。
 * console / chat 为不同 WebView，localStorage 不共享；以 Tauri `get_desktop_ui_lang` + `jachin-desktop-ui-lang-sync` 为准。
 */
export function useDesktopUiLang(): readonly [DesktopUiLang, (next: DesktopUiLang) => void] {
  const [lang, setLangState] = useState<DesktopUiLang>(() =>
    typeof window !== "undefined" ? readDesktopUiLang() : "zh",
  );

  useEffect(() => {
    let cancelled = false;
    let unlisten: (() => void) | undefined;

    const applyFromHost = (next: DesktopUiLang) => {
      applyLangToStorage(next);
      setLangState(next);
    };

    const bootstrap = async () => {
      try {
        const { invoke } = await import("@tauri-apps/api/core");
        const { listen } = await import("@tauri-apps/api/event");
        const host = await invoke<string>("get_desktop_ui_lang");
        if (!cancelled) {
          applyFromHost(host === "en" ? "en" : "zh");
        }
        unlisten = await listen<{ lang: string }>("jachin-desktop-ui-lang-sync", (e) => {
          applyFromHost(e.payload.lang === "en" ? "en" : "zh");
        });
      } catch {
        if (!cancelled) setLangState(readDesktopUiLang());
        try {
          const { listen } = await import("@tauri-apps/api/event");
          unlisten = await listen<{ lang: string }>("jachin-desktop-ui-lang-sync", (e) => {
            applyFromHost(e.payload.lang === "en" ? "en" : "zh");
          });
        } catch {
          /* 纯浏览器预览等非 Tauri 环境 */
        }
      }
    };

    void bootstrap();

    const onSameOriginStorageOrCustom = () => setLangState(readDesktopUiLang());
    window.addEventListener("storage", onSameOriginStorageOrCustom);
    window.addEventListener(DESKTOP_UI_LANG_EVENT, onSameOriginStorageOrCustom as EventListener);
    return () => {
      cancelled = true;
      window.removeEventListener("storage", onSameOriginStorageOrCustom);
      window.removeEventListener(
        DESKTOP_UI_LANG_EVENT,
        onSameOriginStorageOrCustom as EventListener,
      );
      unlisten?.();
    };
  }, []);

  const setLang = useCallback((next: DesktopUiLang) => {
    writeDesktopUiLang(next);
    setLangState(next);
  }, []);

  useEffect(() => {
    if (typeof document === "undefined") return;
    document.documentElement.lang = lang === "en" ? "en" : "zh-Hans";
  }, [lang]);

  return [lang, setLang] as const;
}
