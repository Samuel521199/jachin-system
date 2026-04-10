/**
 * 透明子窗口专用：右下角哨兵 toast（非系统 Notification）
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { invoke } from "@tauri-apps/api/core";
import JachinCore from "../Omni/JachinCore";

export type JachinNotificationPayload = {
  title: string;
  body: string;
};

const AUTO_HIDE_MS = 3000;
const HIDE_WINDOW_AFTER_MS = 380;

function playCyberChime() {
  try {
    const ctx = new AudioContext();
    const o = ctx.createOscillator();
    const g = ctx.createGain();
    o.type = "sine";
    o.frequency.setValueAtTime(880, ctx.currentTime);
    o.frequency.exponentialRampToValueAtTime(1320, ctx.currentTime + 0.06);
    g.gain.setValueAtTime(0.0001, ctx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.08, ctx.currentTime + 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.22);
    o.connect(g);
    g.connect(ctx.destination);
    o.start(ctx.currentTime);
    o.stop(ctx.currentTime + 0.24);
    setTimeout(() => void ctx.close().catch(() => {}), 400);
  } catch {
    /* 非安全上下文或禁音 */
  }
}

export function NotificationApp() {
  const [toast, setToast] = useState<(JachinNotificationPayload & { key: string }) | null>(null);
  const hideTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const hideWindowTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const scheduleHideWindow = useCallback(() => {
    if (hideWindowTimerRef.current) clearTimeout(hideWindowTimerRef.current);
    hideWindowTimerRef.current = setTimeout(() => {
      hideWindowTimerRef.current = null;
      void invoke("jachin_sentry_notify_dismiss").catch(() => {});
    }, HIDE_WINDOW_AFTER_MS);
  }, []);

  const cancelHideWindow = useCallback(() => {
    if (hideWindowTimerRef.current) {
      clearTimeout(hideWindowTimerRef.current);
      hideWindowTimerRef.current = null;
    }
  }, []);

  const dismiss = useCallback(() => {
    if (hideTimerRef.current) {
      clearTimeout(hideTimerRef.current);
      hideTimerRef.current = null;
    }
    setToast(null);
    scheduleHideWindow();
  }, [scheduleHideWindow]);

  const onPayload = useCallback(
    (p: JachinNotificationPayload) => {
      cancelHideWindow();
      playCyberChime();
      const title = typeof p.title === "string" ? p.title : "Jachin";
      const body = typeof p.body === "string" ? p.body : "";
      setToast({ title, body, key: `${Date.now()}-${title.slice(0, 12)}` });
      if (hideTimerRef.current) clearTimeout(hideTimerRef.current);
      hideTimerRef.current = setTimeout(() => {
        hideTimerRef.current = null;
        setToast(null);
        scheduleHideWindow();
      }, AUTO_HIDE_MS);
    },
    [cancelHideWindow, scheduleHideWindow],
  );

  useEffect(() => {
    let unlisten: UnlistenFn | undefined;
    void listen<JachinNotificationPayload>("jachin-notification-show", (ev) => {
      const pl = ev.payload;
      if (!pl || typeof pl !== "object") return;
      onPayload(pl as JachinNotificationPayload);
    })
      .then((fn) => {
        unlisten = fn;
      })
      .catch(() => {});
    return () => {
      unlisten?.();
      if (hideTimerRef.current) clearTimeout(hideTimerRef.current);
      if (hideWindowTimerRef.current) clearTimeout(hideWindowTimerRef.current);
    };
  }, [onPayload]);

  const onClickBar = useCallback(() => {
    cancelHideWindow();
    if (hideTimerRef.current) {
      clearTimeout(hideTimerRef.current);
      hideTimerRef.current = null;
    }
    setToast(null);
    void invoke("jachin_expand_main_from_notification").catch(() => {});
  }, [cancelHideWindow]);

  return (
    <div className="flex h-full w-full items-stretch justify-stretch p-1.5 box-border">
      <AnimatePresence mode="wait">
        {toast ? (
          <motion.button
            key={toast.key}
            type="button"
            layout
            initial={{ opacity: 0, x: 56 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: 48 }}
            transition={{ type: "spring", stiffness: 420, damping: 28 }}
            onClick={onClickBar}
            className="flex w-full min-h-0 cursor-pointer select-none items-center gap-2.5 rounded-xl border border-purple-500/30 bg-black/60 px-3 py-2 text-left shadow-lg shadow-purple-950/40 backdrop-blur-xl outline-none ring-0 focus-visible:ring-2 focus-visible:ring-purple-400/50"
          >
            <div className="pointer-events-none shrink-0 scale-90">
              <JachinCore state="idle" machineState="IDLE" toolFlash={null} className="!h-9 !w-9" />
            </div>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-semibold text-violet-100">{toast.title}</p>
              <p className="mt-0.5 line-clamp-2 text-xs leading-snug text-slate-400">{toast.body || " "}</p>
            </div>
          </motion.button>
        ) : null}
      </AnimatePresence>
    </div>
  );
}
