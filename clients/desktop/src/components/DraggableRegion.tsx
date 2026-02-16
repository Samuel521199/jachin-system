/**
 * DraggableRegion - 使用 startDragging() 实现原生拖动，消除 data-tauri-drag-region 的延迟
 * 拖动时窗口完全跟随鼠标，无卡顿
 * 移动超过阈值才视为拖动，避免点击误触发
 */

import React, { useRef, useCallback, useEffect } from "react";
import { getCurrentWindow } from "@tauri-apps/api/window";

const DRAG_THRESHOLD_PX = 3;

export interface DraggableRegionProps {
  children?: React.ReactNode;
  className?: string;
  style?: React.CSSProperties;
  /** 拖动开始时调用（如播放 PICKED 动画） */
  onDragStart?: () => void;
}

export const DraggableRegion: React.FC<DraggableRegionProps> = ({
  children,
  className,
  style,
  onDragStart,
}) => {
  const downRef = useRef<{ x: number; y: number; started: boolean } | null>(null);

  const handleMove = useCallback(
    (e: MouseEvent) => {
      const d = downRef.current;
      if (!d || d.started) return;
      const dx = e.clientX - d.x;
      const dy = e.clientY - d.y;
      if (dx * dx + dy * dy >= DRAG_THRESHOLD_PX * DRAG_THRESHOLD_PX) {
        d.started = true;
        onDragStart?.();
        void getCurrentWindow().startDragging();
      }
    },
    [onDragStart]
  );

  const handleUp = useCallback(() => {
    downRef.current = null;
    document.removeEventListener("mousemove", handleMove);
    document.removeEventListener("mouseup", handleUp);
  }, [handleMove]);

  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      if (e.button !== 0) return;
      downRef.current = { x: e.clientX, y: e.clientY, started: false };
      document.addEventListener("mousemove", handleMove);
      document.addEventListener("mouseup", handleUp);
    },
    [handleMove, handleUp]
  );

  useEffect(() => {
    return () => {
      document.removeEventListener("mousemove", handleMove);
      document.removeEventListener("mouseup", handleUp);
    };
  }, [handleMove, handleUp]);

  return (
    <div
      className={className}
      style={style}
      onMouseDown={handleMouseDown}
      role="presentation"
    >
      {children}
    </div>
  );
};
