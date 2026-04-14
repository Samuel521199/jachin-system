/** L3 终端与 Omni 对话窗共用的界面语言（localStorage + Tauri settings.json，跨 WebView 由 Rust 广播） */
export type DesktopUiLang = "zh" | "en";

export const DESKTOP_UI_LANG_KEY = "jachin-desktop-ui-lang";

export const DESKTOP_UI_LANG_EVENT = "jachin-desktop-ui-lang";

export function readDesktopUiLang(): DesktopUiLang {
  try {
    return window.localStorage.getItem(DESKTOP_UI_LANG_KEY) === "en" ? "en" : "zh";
  } catch {
    return "zh";
  }
}

export function writeDesktopUiLang(lang: DesktopUiLang): void {
  try {
    window.localStorage.setItem(DESKTOP_UI_LANG_KEY, lang);
  } catch {
    /* noop */
  }
  window.dispatchEvent(new Event(DESKTOP_UI_LANG_EVENT));
  void (async () => {
    try {
      const { invoke } = await import("@tauri-apps/api/core");
      await invoke("set_desktop_ui_lang", { lang });
    } catch {
      /* 非 Tauri / invoke 失败：仅本窗口 localStorage 已更新 */
    }
  })();
}
