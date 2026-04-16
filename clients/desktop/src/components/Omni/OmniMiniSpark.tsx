/**
 * Omni 陪伴态：可拖拽、贴边半隐藏、悬停滑出；完整对话框请用全局快捷键（如 Alt+Shift+Space）
 */
import React, { useCallback, useEffect, useRef } from "react";
import { motion } from "framer-motion";
import { invoke } from "@tauri-apps/api/core";
import { getCurrentWindow, PhysicalPosition } from "@tauri-apps/api/window";

export interface OmniMiniSparkProps {
  /** 双击展开完整 Omni（单点不打开，避免误触） */
  onExpandFull: () => void;
}

export const OmniMiniSpark: React.FC<OmniMiniSparkProps> = ({ onExpandFull }) => {
  const mouseInsideRef = useRef(false);
  const leavePeekTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  /** 丢弃 pointerup 之后仍 resolve 的 outerPosition，避免写入下一轮拖拽 */
  const outerPosRequestIdRef = useRef(0);
  /** ox/oy 在 outerPosition() 解析前为 null，避免 async 间隙内 pointerMove 整段失效 */
  const dragRef = useRef<{
    active: boolean;
    sx: number;
    sy: number;
    ox: number | null;
    oy: number | null;
    moved: boolean;
  } | null>(null);

  const clearLeaveTimer = useCallback(() => {
    if (leavePeekTimerRef.current != null) {
      clearTimeout(leavePeekTimerRef.current);
      leavePeekTimerRef.current = null;
    }
  }, []);

  const schedulePeekAfterLeave = useCallback(() => {
    clearLeaveTimer();
    leavePeekTimerRef.current = setTimeout(() => {
      leavePeekTimerRef.current = null;
      if (!mouseInsideRef.current) {
        void invoke("companion_peek").catch(() => {});
      }
    }, 1100);
  }, [clearLeaveTimer]);

  const reveal = useCallback(() => {
    void invoke("companion_reveal").catch(() => {});
  }, []);

  useEffect(() => {
    const t = setTimeout(() => {
      if (!mouseInsideRef.current) {
        void invoke("companion_peek").catch(() => {});
      }
    }, 900);
    return () => clearTimeout(t);
  }, []);

  useEffect(() => {
    return () => clearLeaveTimer();
  }, [clearLeaveTimer]);

  const onRootEnter = useCallback(() => {
    mouseInsideRef.current = true;
    clearLeaveTimer();
    reveal();
  }, [clearLeaveTimer, reveal]);

  const onRootLeave = useCallback(() => {
    mouseInsideRef.current = false;
    schedulePeekAfterLeave();
  }, [schedulePeekAfterLeave]);

  const onPointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (e.button !== 0) return;
      e.currentTarget.setPointerCapture(e.pointerId);
      clearLeaveTimer();
      const sx = e.screenX;
      const sy = e.screenY;
      dragRef.current = {
        active: true,
        sx,
        sy,
        ox: null,
        oy: null,
        moved: false,
      };
      const reqId = ++outerPosRequestIdRef.current;
      void getCurrentWindow()
        .outerPosition()
        .then((pos) => {
          if (reqId !== outerPosRequestIdRef.current) return;
          const d = dragRef.current;
          if (!d?.active || d.ox !== null) return;
          d.ox = pos.x;
          d.oy = pos.y;
        })
        .catch(() => {
          if (reqId !== outerPosRequestIdRef.current) return;
          const d = dragRef.current;
          if (d?.active && d.ox === null) {
            dragRef.current = null;
          }
        });
    },
    [clearLeaveTimer],
  );

  const onPointerMove = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    const d = dragRef.current;
    if (!d?.active) return;
    const dx = e.screenX - d.sx;
    const dy = e.screenY - d.sy;
    if (Math.hypot(dx, dy) > 4) d.moved = true;
    if (d.ox == null || d.oy == null) return;
    void getCurrentWindow().setPosition(new PhysicalPosition(d.ox + dx, d.oy + dy));
  }, []);

  const endDrag = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      const d = dragRef.current;
      if (!d?.active) {
        try {
          e.currentTarget.releasePointerCapture(e.pointerId);
        } catch {
          /* ignore */
        }
        return;
      }
      outerPosRequestIdRef.current += 1;
      dragRef.current = null;
      try {
        e.currentTarget.releasePointerCapture(e.pointerId);
      } catch {
        /* ignore */
      }
      if (d.moved) {
        void (async () => {
          try {
            const w = getCurrentWindow();
            const p = await w.outerPosition();
            await invoke("companion_set_dock_position", { x: p.x, y: p.y });
            if (!mouseInsideRef.current) {
              void invoke("companion_peek").catch(() => {});
            }
          } catch {
            /* ignore */
          }
        })();
      } else {
        schedulePeekAfterLeave();
      }
    },
    [schedulePeekAfterLeave],
  );

  return (
    <motion.div
      initial={{ scale: 0, opacity: 0 }}
      animate={{ scale: 1, opacity: 1 }}
      exit={{ scale: 0, opacity: 0 }}
      transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
      role="presentation"
      data-tauri-drag-region="false"
      className="flex h-full w-full min-h-0 cursor-grab select-none flex-col items-center justify-center overflow-visible bg-transparent active:cursor-grabbing"
      style={{ touchAction: "none" }}
      onMouseEnter={onRootEnter}
      onMouseLeave={onRootLeave}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      onDoubleClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
        onExpandFull();
      }}
      title="拖动移动 · 靠边框自动半隐藏 · 悬停露出 · 双击或 Alt+Shift+Space 打开完整 Omni"
    >
      <motion.div
        aria-hidden
        className="pointer-events-none relative flex h-[34px] w-[34px] shrink-0 items-center justify-center"
        animate={{ rotate: [0, 360] }}
        transition={{ duration: 28, repeat: Infinity, ease: "linear" }}
      >
        <motion.span
          className="absolute inset-0 rounded-full border border-cyan-400/25"
          animate={{
            scale: [1, 1.35, 1],
            opacity: [0.35, 0.08, 0.35],
          }}
          transition={{ duration: 2.8, repeat: Infinity, ease: "easeInOut" }}
        />
        <motion.span
          className="absolute inset-[-3px] rounded-full border border-violet-400/20"
          animate={{
            scale: [1.15, 1, 1.15],
            opacity: [0.12, 0.35, 0.12],
          }}
          transition={{ duration: 3.4, repeat: Infinity, ease: "easeInOut", delay: 0.4 }}
        />
        <motion.div
          animate={{
            scale: [1, 1.12, 1],
            opacity: [0.72, 1, 0.72],
            boxShadow: [
              "0 0 8px rgba(34,211,238,0.35), 0 0 18px rgba(139,92,246,0.25), inset 0 0 12px rgba(34,211,238,0.15)",
              "0 0 14px rgba(34,211,238,0.65), 0 0 28px rgba(139,92,246,0.45), inset 0 0 14px rgba(167,139,250,0.2)",
              "0 0 8px rgba(34,211,238,0.35), 0 0 18px rgba(139,92,246,0.25), inset 0 0 12px rgba(34,211,238,0.15)",
            ],
          }}
          transition={{ duration: 3.6, repeat: Infinity, ease: "easeInOut" }}
          className="relative h-[30px] w-[30px] rounded-full border border-cyan-400/45"
          style={{
            background:
              "radial-gradient(circle at 35% 30%, rgba(167,139,250,0.95) 0%, rgba(34,211,238,0.55) 45%, rgba(6,182,212,0.25) 100%)",
          }}
        />
      </motion.div>
    </motion.div>
  );
};

export default OmniMiniSpark;
