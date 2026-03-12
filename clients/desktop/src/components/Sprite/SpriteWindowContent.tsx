/**
 * SpriteWindowContent - 精灵窗口专用，仅渲染宠物本体
 *
 * 精灵窗口（sprite.html）只显示宠物，无 InputBar。
 * 聊天通过右键菜单「打开聊天」进入独立 chat 窗口。
 */

import React, { useState, useRef, useEffect } from "react";
import { getCurrentWindow, PhysicalPosition } from "@tauri-apps/api/window";
import { WebviewWindow } from "@tauri-apps/api/webviewWindow";
import { AvatarContainer } from "./AvatarContainer";
import { PetScreenWithLayout } from "../PetScreen";
import { FloatingMenu, defaultMenuActions } from "../Menu/FloatingMenu";
import { useSpriteStore } from "../../store/spriteStore";

const PERSIST_KEY = "jachin-sprite-persona";

export const SpriteWindowContent: React.FC = () => {
  const { avatarId } = useSpriteStore();
  const [menuOpen, setMenuOpen] = useState(false);

  // 监听控制台 Persona 的修改，跨窗口同步（控制台与精灵窗口是独立 WebView）
  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key === PERSIST_KEY && e.newValue) {
        useSpriteStore.persist.rehydrate();
      }
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);
  const [menuPosition, setMenuPosition] = useState({ x: 0, y: 0 });
  const containerRef = useRef<HTMLDivElement>(null);

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

  // Pixi 模式：PetScreenWithLayout 已不含 InputBar
  if (avatarId === "pixi") {
    return <PetScreenWithLayout />;
  }

  // Emoji/Rive 模式：仅 Avatar + 右键菜单
  return (
    <div
      ref={containerRef}
      className="w-full h-full bg-transparent overflow-hidden"
      style={{ userSelect: "none" }}
    >
      <div
        className="absolute inset-0 flex items-center justify-center"
        data-tauri-drag-region
      >
        <AvatarContainer
          onContextMenu={handleContextMenu}
          onDoubleClick={handleDoubleClick}
        />
      </div>
      <FloatingMenu
        isOpen={menuOpen}
        position={menuPosition}
        actions={defaultMenuActions}
        onClose={() => setMenuOpen(false)}
      />
    </div>
  );
};
