/**
 * L3 控制台右上角：Jachin 哨兵消息中心（未读/已读，与右下角弹窗同源持久化）
 *
 * 注意：父级 Horizon 的 motion 条有 clip-path，子元素用 absolute 会被裁切到不可见。
 * 面板使用 createPortal 挂到 body + position:fixed，并单独处理点击外部关闭。
 */
import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Bell, CheckCheck } from "lucide-react";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { cn } from "../../utils/cn";
import {
  fetchConsoleInbox,
  markAllConsoleInboxRead,
  markConsoleInboxRead,
  type ConsoleInboxItem,
} from "../../lib/consoleInboxApi";
import type { DesktopUiLang } from "../../utils/desktopUiLang";
import { desktopHorizon } from "../../utils/desktopUiI18n";

type Filter = "all" | "unread" | "read";

function formatTime(ms: number, lang: DesktopUiLang): string {
  try {
    const d = new Date(ms);
    if (lang === "en") {
      return d.toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" });
    }
    return d.toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
  } catch {
    return "—";
  }
}

export function ConsoleInboxCenter({ lang }: { lang: DesktopUiLang }) {
  const hz = desktopHorizon[lang].inbox;
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState<Filter>("all");
  const [items, setItems] = useState<ConsoleInboxItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [panelStyle, setPanelStyle] = useState<React.CSSProperties>({});
  const buttonRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    setLoading(true);
    const list = await fetchConsoleInbox();
    setItems(list);
    setLoading(false);
  }, []);

  /** 首屏不阻塞；打开面板或事件推送时再拉取 */
  useEffect(() => {
    void load();
  }, [load]);

  const updatePanelPosition = useCallback(() => {
    const btn = buttonRef.current;
    if (!btn) return;
    const r = btn.getBoundingClientRect();
    const margin = 8;
    setPanelStyle({
      position: "fixed",
      top: r.bottom + 6,
      right: Math.max(margin, document.documentElement.clientWidth - r.right),
      width: "min(calc(100vw - 2rem), 22rem)",
      maxWidth: "calc(100vw - 2rem)",
      zIndex: 5000,
    });
  }, []);

  useLayoutEffect(() => {
    if (!open) return;
    updatePanelPosition();
  }, [open, updatePanelPosition]);

  useEffect(() => {
    if (!open) return;
    const onResize = () => updatePanelPosition();
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [open, updatePanelPosition]);

  useEffect(() => {
    let u: UnlistenFn | undefined;
    void listen("jachin-inbox-updated", () => {
      void load();
    })
      .then((fn) => {
        u = fn;
      })
      .catch(() => {});
    return () => {
      u?.();
    };
  }, [load]);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      const t = e.target as Node;
      if (buttonRef.current?.contains(t) || panelRef.current?.contains(t)) return;
      setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc, true);
    window.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc, true);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const filtered = useMemo(() => {
    if (filter === "unread") return items.filter((i) => !i.read);
    if (filter === "read") return items.filter((i) => i.read);
    return items;
  }, [items, filter]);

  const unreadCount = useMemo(() => items.filter((i) => !i.read).length, [items]);

  const onRowClick = async (it: ConsoleInboxItem) => {
    if (!it.read) {
      await markConsoleInboxRead(it.id);
      setItems((prev) => prev.map((x) => (x.id === it.id ? { ...x, read: true } : x)));
    }
  };

  const onMarkAll = async () => {
    if (unreadCount === 0) return;
    const ok = await markAllConsoleInboxRead();
    if (ok) {
      setItems((prev) => prev.map((x) => ({ ...x, read: true })));
    }
  };

  const panelNode =
    open ? (
      <div
        ref={panelRef}
        style={panelStyle}
        className={cn(
          "rounded-lg border border-cyan-500/30 bg-black/95 py-2 shadow-[0_16px_48px_rgba(0,0,0,0.75),0_0_0_1px_rgba(34,211,238,0.12)] backdrop-blur-xl",
          "[clip-path:polygon(0_0,calc(100%-10px)_0,100%_10px,100%_100%,0_100%)]"
        )}
        role="dialog"
        aria-label={hz.heading}
        onMouseDown={(e) => e.stopPropagation()}
      >
          <div className="flex items-center justify-between border-b border-white/5 px-3 pb-2 pt-0.5">
            <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-cyan-200/90">{hz.heading}</p>
            <button
              type="button"
              onClick={onMarkAll}
              disabled={unreadCount === 0}
              title={hz.markAll}
              className="flex items-center gap-1 rounded border border-white/10 bg-white/5 px-2 py-0.5 text-[10px] text-slate-300 transition hover:border-cyan-500/35 hover:text-cyan-200 disabled:cursor-not-allowed disabled:opacity-30"
            >
              <CheckCheck className="h-3 w-3" />
              {hz.markAll}
            </button>
          </div>
          <div className="flex gap-0.5 border-b border-white/5 px-2 py-1.5">
            {(["all", "unread", "read"] as const).map((f) => (
              <button
                key={f}
                type="button"
                onClick={() => setFilter(f)}
                className={cn(
                  "flex-1 rounded px-2 py-1 text-[10px] font-mono transition",
                  filter === f
                    ? "bg-cyan-500/20 text-cyan-100"
                    : "text-slate-500 hover:bg-white/5 hover:text-slate-300"
                )}
              >
                {f === "all" ? hz.tabAll : f === "unread" ? hz.tabUnread : hz.tabRead}
              </button>
            ))}
          </div>
          <ul className="max-h-[min(60vh,22rem)] overflow-y-auto overscroll-contain px-1 py-1 no-scrollbar">
            {loading ? (
              <li className="px-3 py-6 text-center text-[11px] text-slate-500">{hz.loading}</li>
            ) : filtered.length === 0 ? (
              <li className="px-3 py-6 text-center text-[11px] text-slate-500">{hz.empty}</li>
            ) : (
              filtered.map((it) => (
                <li key={it.id}>
                  <button
                    type="button"
                    onClick={() => void onRowClick(it)}
                    className={cn(
                      "w-full rounded-md border border-transparent px-2.5 py-2 text-left transition hover:border-cyan-500/20 hover:bg-cyan-500/10",
                      !it.read && "border-cyan-500/15 bg-cyan-500/[0.07]"
                    )}
                  >
                    <div className="flex items-start gap-2">
                      <span
                        className={cn("mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full", it.read ? "bg-slate-600" : "bg-cyan-400 shadow-[0_0_6px_rgba(34,211,238,0.8)]")}
                        aria-hidden
                      />
                      <div className="min-w-0 flex-1">
                        <p className="line-clamp-1 text-xs font-medium text-cyan-50/95">{it.title || "Jachin"}</p>
                        {it.body ? (
                          <p className="mt-0.5 line-clamp-3 text-[11px] leading-relaxed text-slate-400">{it.body}</p>
                        ) : null}
                        <p className="mt-1 font-mono text-[9px] text-cyan-600/80">{formatTime(it.created_at_ms, lang)}</p>
                      </div>
                    </div>
                  </button>
                </li>
              ))
            )}
          </ul>
      </div>
    ) : null;

  return (
    <div className="relative">
      <button
        ref={buttonRef}
        type="button"
        title={hz.title}
        aria-label={hz.title}
        aria-expanded={open}
        onClick={() => {
          setOpen((o) => {
            const next = !o;
            if (next) {
              void load();
              queueMicrotask(() => updatePanelPosition());
            }
            return next;
          });
        }}
        className="relative flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-cyan-500/20 text-cyan-300/80 transition-all hover:border-cyan-400/50 hover:bg-cyan-500/10 hover:text-cyan-100"
      >
        <Bell className="h-3.5 w-3.5" strokeWidth={2.2} />
        {unreadCount > 0 ? (
          <span
            className="absolute -right-0.5 -top-0.5 flex h-4 min-w-[1rem] items-center justify-center rounded bg-rose-500/95 px-1 font-mono text-[9px] font-bold leading-none text-white shadow-[0_0_8px_rgba(244,63,94,0.65)]"
            aria-hidden
          >
            {unreadCount > 99 ? "99+" : unreadCount}
          </span>
        ) : null}
      </button>

      {typeof document !== "undefined" && panelNode ? createPortal(panelNode, document.body) : null}
    </div>
  );
}
