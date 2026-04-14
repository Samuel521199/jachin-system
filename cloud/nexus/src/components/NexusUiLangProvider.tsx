"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  NEXUS_UI_LANG_STORAGE_KEY,
  readNexusUiLangFromStorage,
  type NexusUiLang,
} from "@/lib/nexus-ui-i18n";

type NexusUiLangContextValue = {
  lang: NexusUiLang;
  setLang: (next: NexusUiLang) => void;
};

const NexusUiLangContext = createContext<NexusUiLangContextValue | null>(null);

export function NexusUiLangProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<NexusUiLang>("zh");
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
    setLangState(readNexusUiLangFromStorage());
  }, []);

  useEffect(() => {
    if (!mounted) return;
    document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
  }, [lang, mounted]);

  const setLang = useCallback((next: NexusUiLang) => {
    setLangState(next);
    try {
      window.localStorage.setItem(NEXUS_UI_LANG_STORAGE_KEY, next);
    } catch {
      /* noop */
    }
    window.dispatchEvent(new Event("jachin-nexus-ui-lang"));
  }, []);

  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key === NEXUS_UI_LANG_STORAGE_KEY && e.newValue) {
        setLangState(e.newValue === "en" ? "en" : "zh");
      }
    };
    const onCustom = () => setLangState(readNexusUiLangFromStorage());
    window.addEventListener("storage", onStorage);
    window.addEventListener("jachin-nexus-ui-lang", onCustom);
    return () => {
      window.removeEventListener("storage", onStorage);
      window.removeEventListener("jachin-nexus-ui-lang", onCustom);
    };
  }, []);

  const value = useMemo(() => ({ lang, setLang }), [lang, setLang]);

  return (
    <NexusUiLangContext.Provider value={value}>{children}</NexusUiLangContext.Provider>
  );
}

export function useNexusUiLang(): NexusUiLangContextValue {
  const ctx = useContext(NexusUiLangContext);
  if (!ctx) {
    throw new Error("useNexusUiLang must be used within NexusUiLangProvider");
  }
  return ctx;
}

/** 允许在尚未包裹 Provider 的边界内兜底（不推荐长期使用） */
export function useNexusUiLangOptional(): NexusUiLangContextValue | null {
  return useContext(NexusUiLangContext);
}
