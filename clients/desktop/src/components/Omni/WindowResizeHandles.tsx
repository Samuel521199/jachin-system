/**
 * 无边框（decorations: false）窗口无系统缩放边；在窗口四周与斜角放置命中区，调用 Tauri startResizeDragging。
 */
import React from "react";
import { getCurrentWindow } from "@tauri-apps/api/window";

const CORNER_PX = 12;
const EDGE_PX = 6;

type ResizeDir =
  | "East"
  | "North"
  | "NorthEast"
  | "NorthWest"
  | "South"
  | "SouthEast"
  | "SouthWest"
  | "West";

function handleResizeMouseDown(dir: ResizeDir) {
  return (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    void getCurrentWindow().startResizeDragging(dir);
  };
}

const base = "absolute pointer-events-auto z-40";

export const WindowResizeHandles: React.FC = () => (
  <div className="pointer-events-none absolute inset-0 overflow-visible" aria-hidden>
    <div
      className={`${base} top-0 left-0 cursor-nwse-resize`}
      style={{ width: CORNER_PX, height: CORNER_PX }}
      onMouseDown={handleResizeMouseDown("NorthWest")}
    />
    <div
      className={`${base} top-0 right-0 cursor-nesw-resize`}
      style={{ width: CORNER_PX, height: CORNER_PX }}
      onMouseDown={handleResizeMouseDown("NorthEast")}
    />
    <div
      className={`${base} bottom-0 left-0 cursor-nesw-resize`}
      style={{ width: CORNER_PX, height: CORNER_PX }}
      onMouseDown={handleResizeMouseDown("SouthWest")}
    />
    <div
      className={`${base} bottom-0 right-0 cursor-nwse-resize`}
      style={{ width: CORNER_PX, height: CORNER_PX }}
      onMouseDown={handleResizeMouseDown("SouthEast")}
    />
    <div
      className={`${base} top-0 cursor-ns-resize`}
      style={{ left: CORNER_PX, right: CORNER_PX, height: EDGE_PX }}
      onMouseDown={handleResizeMouseDown("North")}
    />
    <div
      className={`${base} bottom-0 cursor-ns-resize`}
      style={{ left: CORNER_PX, right: CORNER_PX, height: EDGE_PX }}
      onMouseDown={handleResizeMouseDown("South")}
    />
    <div
      className={`${base} left-0 cursor-ew-resize`}
      style={{ top: CORNER_PX, bottom: CORNER_PX, width: EDGE_PX }}
      onMouseDown={handleResizeMouseDown("West")}
    />
    <div
      className={`${base} right-0 cursor-ew-resize`}
      style={{ top: CORNER_PX, bottom: CORNER_PX, width: EDGE_PX }}
      onMouseDown={handleResizeMouseDown("East")}
    />
  </div>
);
