/**
 * PetScreenWithLayout - 桌面精灵窗口布局
 *
 * 精灵窗口仅渲染桌面宠物，无 InputBar。聊天通过右键菜单「打开聊天」进入独立 chat 窗口。
 * 用法：在 sprite.html 中渲染 <PetScreenWithLayout />
 */

import React, { useState, useRef } from "react";
import { getCurrentWindow, PhysicalPosition } from "@tauri-apps/api/window";
import { WebviewWindow } from "@tauri-apps/api/webviewWindow";
import { PetScreen, type PetScreenHandle } from "./PetScreen";
import { DraggableRegion } from "../DraggableRegion";
import { FloatingMenu, defaultMenuActions } from "../Menu/FloatingMenu";

export const PetScreenWithLayout: React.FC = () => {
  const [menuOpen, setMenuOpen] = useState(false);
  const [menuPosition, setMenuPosition] = useState({ x: 0, y: 0 });

  const containerRef = useRef<HTMLDivElement>(null);
  const petScreenRef = useRef<PetScreenHandle | null>(null);

  const handleContextMenu = (e: React.MouseEvent) => {
    e.preventDefault();
    setMenuPosition({ x: e.clientX, y: e.clientY });
    setMenuOpen(true);
  };

  const handleDoubleClick = async () => {
    try {
      const chatWindow = await WebviewWindow.getByLabel("chat");
      if (chatWindow) {
        const spritePos = await getCurrentWindow().innerPosition();
        await chatWindow.setPosition(new PhysicalPosition(spritePos.x + 140, spritePos.y));
        await chatWindow.show();
        await chatWindow.setFocus();
      }
    } catch (error) {
      console.error("Failed to open chat window:", error);
    }
  };

  return (
    <div
      ref={containerRef}
      className="w-full h-full bg-transparent overflow-hidden"
      style={{ userSelect: "none" }}
    >
      {/* Pixi 精灵区域 - 使用 startDragging 实现无延迟拖动 */}
      <DraggableRegion
        className="absolute inset-0"
        onDragStart={() => petScreenRef.current?.playPicked()}
      >
        <PetScreen
          ref={petScreenRef}
          onContextMenu={handleContextMenu}
          onDoubleClick={handleDoubleClick}
        />
      </DraggableRegion>

      {/* 右键菜单：打开聊天、设置等 */}
      <FloatingMenu
        isOpen={menuOpen}
        position={menuPosition}
        actions={defaultMenuActions}
        onClose={() => setMenuOpen(false)}
      />
    </div>
  );
};
