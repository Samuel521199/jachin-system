"use client";

import { Check, ChevronDown, Globe } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useNexusUiLang } from "@/components/NexusUiLangProvider";

/** 顶栏/落地页共用的语言下拉（当前语言 + 列表选择） */
export function NexusLanguageMenu() {
  const { lang, setLang } = useNexusUiLang();
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
    <div ref={ref} className="relative">
      <button
        type="button"
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-label={
          lang === "zh"
            ? "界面语言：中文，打开语言列表"
            : "Interface language: English, open language list"
        }
        onClick={() => setOpen((o) => !o)}
        className={
          "flex items-center gap-1.5 rounded-full border border-white/10 bg-white/5 px-3 py-1.5 " +
          "text-sm font-medium text-gray-200 backdrop-blur-md transition-all " +
          "hover:border-white/20 hover:bg-white/10 hover:text-white"
        }
      >
        <Globe size={16} className={lang === "en" ? "text-cyan-400" : "text-purple-400"} />
        <span>{lang === "zh" ? "中文" : "English"}</span>
        <ChevronDown
          className={`h-4 w-4 shrink-0 text-white/50 transition-transform ${open ? "rotate-180" : ""}`}
          aria-hidden
        />
      </button>
      {open && (
        <div
          role="listbox"
          aria-label={lang === "zh" ? "选择语言" : "Choose language"}
          className={
            "absolute right-0 top-[calc(100%+8px)] z-[60] min-w-[168px] overflow-hidden rounded-xl " +
            "border border-white/10 bg-black/90 py-1 shadow-2xl backdrop-blur-xl"
          }
        >
          <button
            type="button"
            role="option"
            aria-selected={lang === "zh"}
            className={
              "flex w-full items-center justify-between gap-3 px-4 py-2.5 text-left text-sm " +
              "text-white transition-colors hover:bg-white/5 " +
              (lang === "zh" ? "bg-white/[0.06]" : "")
            }
            onClick={() => {
              setLang("zh");
              setOpen(false);
            }}
          >
            <span>中文</span>
            {lang === "zh" ? <Check className="h-4 w-4 shrink-0 text-cyan-400" aria-hidden /> : null}
          </button>
          <button
            type="button"
            role="option"
            aria-selected={lang === "en"}
            className={
              "flex w-full items-center justify-between gap-3 px-4 py-2.5 text-left text-sm " +
              "text-white transition-colors hover:bg-white/5 " +
              (lang === "en" ? "bg-white/[0.06]" : "")
            }
            onClick={() => {
              setLang("en");
              setOpen(false);
            }}
          >
            <span>English</span>
            {lang === "en" ? <Check className="h-4 w-4 shrink-0 text-cyan-400" aria-hidden /> : null}
          </button>
        </div>
      )}
    </div>
  );
}
