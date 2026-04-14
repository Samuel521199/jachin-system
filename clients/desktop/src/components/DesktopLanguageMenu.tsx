/**
 * 桌面端（L3 控制台 Horizon）语言下拉 — 与 Omni 共用 localStorage
 */
import { Check, ChevronDown, Globe } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useDesktopUiLang } from "../hooks/useDesktopUiLang";

export function DesktopLanguageMenu() {
  const [lang, setLang] = useDesktopUiLang();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  return (
    <div ref={ref} className="relative flex items-center">
      <button
        type="button"
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-label={lang === "zh" ? "界面语言" : "Interface language"}
        onClick={() => setOpen((o) => !o)}
        className={
          "flex items-center gap-1 rounded border border-white/10 bg-white/5 px-2 py-0.5 " +
          "text-[11px] font-medium text-slate-300 transition-colors hover:border-white/20 hover:bg-white/10 hover:text-white"
        }
      >
        <Globe className="h-3.5 w-3.5 shrink-0 text-cyan-400/90" aria-hidden />
        <span>{lang === "zh" ? "中文" : "EN"}</span>
        <ChevronDown
          className={`h-3 w-3 shrink-0 text-slate-500 transition-transform ${open ? "rotate-180" : ""}`}
          aria-hidden
        />
      </button>
      {open && (
        <div
          role="listbox"
          className={
            "absolute right-0 top-[calc(100%+6px)] z-[200] min-w-[140px] overflow-hidden rounded-lg " +
            "border border-white/10 bg-black/95 py-1 text-[11px] shadow-xl backdrop-blur-md"
          }
        >
          <button
            type="button"
            role="option"
            aria-selected={lang === "zh"}
            className={`flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-slate-200 hover:bg-white/5 ${
              lang === "zh" ? "bg-white/[0.06]" : ""
            }`}
            onClick={() => {
              setLang("zh");
              setOpen(false);
            }}
          >
            中文
            {lang === "zh" ? <Check className="h-3.5 w-3.5 text-cyan-400" /> : null}
          </button>
          <button
            type="button"
            role="option"
            aria-selected={lang === "en"}
            className={`flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-slate-200 hover:bg-white/5 ${
              lang === "en" ? "bg-white/[0.06]" : ""
            }`}
            onClick={() => {
              setLang("en");
              setOpen(false);
            }}
          >
            English
            {lang === "en" ? <Check className="h-3.5 w-3.5 text-cyan-400" /> : null}
          </button>
        </div>
      )}
    </div>
  );
}
