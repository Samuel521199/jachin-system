import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { invoke } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { AiState, JachinOrb } from "./JachinOrb";

export interface OrbWindowProps {
  state: AiState;
  onExpandFull: () => void;
  onQuickSend?: (text: string) => void;
  onBargeIn?: () => void;
  isRecording?: boolean;
  onVoiceStart?: () => void;
  onVoiceStop?: () => void;
}

const SINGLE_CLICK_DELAY_MS = 220;

export function OrbWindow({
  state,
  onExpandFull,
  onQuickSend,
  onBargeIn,
  isRecording = false,
  onVoiceStart,
  onVoiceStop,
}: OrbWindowProps) {
  const [inputOpen, setInputOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [tips, setTips] = useState<string[]>([]);
  const clickTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const saveDockDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const stateText = useMemo(() => {
    if (state === "listening") return "LISTENING";
    if (state === "thinking") return "THINKING";
    if (state === "speaking") return "SPEAKING";
    return "IDLE";
  }, [state]);

  const saveDockPosition = useCallback(async () => {
    try {
      const p = await getCurrentWindow().outerPosition();
      await invoke("companion_set_dock_position", { x: p.x, y: p.y });
    } catch {
      // noop
    }
  }, []);

  useEffect(() => {
    let unlisten: (() => void) | undefined;
    void getCurrentWindow()
      .onMoved(() => {
        if (saveDockDebounceRef.current) clearTimeout(saveDockDebounceRef.current);
        saveDockDebounceRef.current = setTimeout(() => {
          void saveDockPosition();
        }, 150);
      })
      .then((fn) => {
        unlisten = fn;
      })
      .catch(() => {});
    return () => {
      unlisten?.();
      if (saveDockDebounceRef.current) clearTimeout(saveDockDebounceRef.current);
      if (clickTimerRef.current) clearTimeout(clickTimerRef.current);
    };
  }, [saveDockPosition]);

  const onDragRegionClick = useCallback(() => {
    if ((state === "speaking" || state === "thinking") && onBargeIn) {
      onBargeIn();
      return;
    }
    if (clickTimerRef.current) clearTimeout(clickTimerRef.current);
    clickTimerRef.current = setTimeout(() => {
      clickTimerRef.current = null;
      onExpandFull();
    }, SINGLE_CLICK_DELAY_MS);
  }, [state, onBargeIn, onExpandFull]);

  const onDragRegionDoubleClick = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    if (clickTimerRef.current) {
      clearTimeout(clickTimerRef.current);
      clickTimerRef.current = null;
    }
    setInputOpen((v) => !v);
  }, []);

  const onSubmitQuick = useCallback(() => {
    const text = draft.trim();
    if (!text) return;
    onQuickSend?.(text);
    setTips((prev) => [text, ...prev].slice(0, 3));
    setDraft("");
    setInputOpen(false);
  }, [draft, onQuickSend]);

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
      role="presentation"
      className="relative flex h-full w-full min-h-0 select-none flex-col items-center justify-center overflow-visible bg-transparent px-2 py-3"
    >
      <div
        data-tauri-drag-region
        className="absolute inset-0 z-50 cursor-grab active:cursor-grabbing"
        onClick={onDragRegionClick}
        onDoubleClick={onDragRegionDoubleClick}
        title="拖拽移动 · 单击展开 Omni · 朗读中单击打断 · 双击快捷输入"
      />

      <div className="pointer-events-none relative z-10 flex shrink-0 flex-col items-center gap-1.5">
        <div className="flex shrink-0 items-center justify-center p-3">
          <JachinOrb state={state} />
        </div>

        <div className="min-h-[18px] text-[10px] tracking-[0.22em] text-cyan-200/80">{stateText}</div>

        <AnimatePresence>
          {tips.length > 0 ? (
            <motion.div
              key="tips"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 8 }}
              className="flex w-[210px] flex-col gap-1.5"
            >
              {tips.map((t, i) => (
                <div
                  key={`${t}-${i}`}
                  className="truncate rounded-md border border-cyan-400/30 bg-slate-950/70 px-2 py-1 text-[10px] text-cyan-100/85 shadow-[0_0_12px_rgba(34,211,238,0.22)]"
                >
                  {t}
                </div>
              ))}
            </motion.div>
          ) : null}
        </AnimatePresence>
      </div>

      <div className="pointer-events-auto relative z-[60] mt-1" data-tauri-drag-region="false">
        <button
          type="button"
          data-tauri-drag-region="false"
          className={`rounded-md border px-3 py-1 text-[11px] font-medium tracking-[0.08em] transition ${
            isRecording
              ? "border-rose-400/70 bg-rose-500/20 text-rose-100 hover:bg-rose-500/30"
              : "border-cyan-400/60 bg-cyan-500/15 text-cyan-100 hover:bg-cyan-500/25"
          }`}
          onPointerDown={(e) => {
            e.preventDefault();
            e.stopPropagation();
            if (clickTimerRef.current) {
              clearTimeout(clickTimerRef.current);
              clickTimerRef.current = null;
            }
          }}
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            if (clickTimerRef.current) {
              clearTimeout(clickTimerRef.current);
              clickTimerRef.current = null;
            }
            if (isRecording) onVoiceStop?.();
            else onVoiceStart?.();
          }}
          title={isRecording ? "结束语音录制" : "开始语音录制"}
        >
          {isRecording ? "结束语音" : "语音输入"}
        </button>
      </div>

      <AnimatePresence>
        {inputOpen ? (
          <motion.div
            key="quick-input"
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 10 }}
            className="pointer-events-auto relative z-[60] mt-1 flex w-[220px] items-center gap-1.5 rounded-lg border border-cyan-400/35 bg-slate-950/85 p-1.5 backdrop-blur"
            data-companion-input
            data-tauri-drag-region="false"
          >
            <input
              autoFocus
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") onSubmitQuick();
                if (e.key === "Escape") setInputOpen(false);
              }}
              placeholder="快速发给 Jachin..."
              className="w-full bg-transparent px-1 text-[11px] text-cyan-100 placeholder:text-cyan-200/45 outline-none"
            />
            <button
              type="button"
              className="rounded border border-cyan-400/45 px-2 py-0.5 text-[10px] text-cyan-100 hover:bg-cyan-500/15"
              onClick={onSubmitQuick}
            >
              发送
            </button>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </motion.div>
  );
}

export default OrbWindow;
