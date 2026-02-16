/**
 * AvatarContainer - 精灵形象容器
 * 
 * Aero Prism 风格的精灵形象组件
 * - 120x120px 透明容器
 * - 支持拖拽
 * - 支持右键菜单
 * - 支持双击动画
 */

import React, { useState, useRef, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { useRive } from "@rive-app/react-canvas";
import { useSpriteStore } from "../../store/spriteStore";
import { cn } from "../../utils/cn";

interface AvatarContainerProps {
  onContextMenu?: (event: React.MouseEvent) => void;
  onDoubleClick?: () => void;
}

export const AvatarContainer: React.FC<AvatarContainerProps> = ({
  onContextMenu,
  onDoubleClick,
}) => {
  const { state, avatarId } = useSpriteStore();
  const [hasRiveFile, setHasRiveFile] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const [isHappy, setIsHappy] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  // 尝试加载 Rive 动画（仅当 avatarId 为 rive 时）
  const useRiveFile = avatarId === "rive";
  const { rive, RiveComponent } = useRive({
    src: "/jachin_sprite.riv",
    stateMachines: "State Machine 1",
    autoplay: true,
    onLoad: () => {
      setHasRiveFile(true);
    },
    onLoadError: () => {
      setHasRiveFile(false);
    },
  });

  // 双击处理
  const handleDoubleClick = async () => {
    setIsHappy(true);
    setTimeout(() => setIsHappy(false), 2000);
    onDoubleClick?.();
  };

  // 右键处理
  const handleContextMenu = (e: React.MouseEvent) => {
    e.preventDefault();
    onContextMenu?.(e);
  };

  // 获取状态视觉
  const getEmojiForAvatar = () => {
    switch (avatarId) {
      case "emoji-friendly":
        return "😊";
      case "emoji-tech":
        return "🔮";
      case "emoji-default":
      default:
        return "🤖";
    }
  };

  const getStateVisual = () => {
    const emoji = getEmojiForAvatar();
    switch (state) {
      case "thinking":
        return {
          emoji: avatarId === "emoji-default" ? "🤔" : emoji,
          glow: "shadow-[0_0_20px_rgba(139,92,246,0.6)]",
          color: "from-purple-500/40 to-blue-500/40",
        };
      case "listening":
        return {
          emoji: avatarId === "emoji-default" ? "👂" : emoji,
          glow: "shadow-[0_0_20px_rgba(34,197,94,0.6)]",
          color: "from-green-500/40 to-emerald-500/40",
        };
      case "speaking":
        return {
          emoji: avatarId === "emoji-default" ? "💬" : emoji,
          glow: "shadow-[0_0_20px_rgba(234,179,8,0.6)]",
          color: "from-yellow-500/40 to-orange-500/40",
        };
      default:
        return {
          emoji,
          glow: "shadow-[0_0_15px_rgba(139,92,246,0.4)]",
          color: "from-purple-500/30 to-pink-500/30",
        };
    }
  };

  const visual = getStateVisual();

  return (
    <motion.div
      ref={containerRef}
      className="w-[120px] h-[120px] flex items-center justify-center cursor-move bg-transparent"
      data-tauri-drag-region
      onDoubleClick={handleDoubleClick}
      onContextMenu={handleContextMenu}
      onMouseDown={() => setIsDragging(true)}
      onMouseUp={() => setIsDragging(false)}
      animate={{
        scale: isHappy ? [1, 1.1, 1] : isDragging ? 0.95 : 1,
      }}
      transition={{
        duration: 0.2,
        ease: "easeOut",
      }}
      style={{ userSelect: "none" }}
    >
      {/* 呼吸光晕背景 */}
      <motion.div
        className={cn(
          "absolute inset-0 rounded-full bg-gradient-to-br",
          visual.color,
          visual.glow
        )}
        animate={{
          scale: [1, 1.05, 1],
          opacity: [0.3, 0.5, 0.3],
        }}
        transition={{
          duration: 3,
          repeat: Infinity,
          ease: "easeInOut",
        }}
      />

      {/* 动画内容 */}
      <div className="relative w-full h-full flex items-center justify-center">
        {useRiveFile && hasRiveFile && rive ? (
          // 使用 Rive 动画
          <RiveComponent style={{ width: "100%", height: "100%" }} />
        ) : (
          // CSS 动画占位符
          <motion.div
            className="w-full h-full flex items-center justify-center rounded-full"
            animate={isHappy ? { rotate: [0, 10, -10, 0] } : {}}
            transition={{ duration: 0.5 }}
          >
            <div className="text-6xl drop-shadow-lg">{visual.emoji}</div>
          </motion.div>
        )}
      </div>

      {/* 状态指示环（仅在非 idle 状态显示） */}
      <AnimatePresence>
        {state !== "idle" && (
          <motion.div
            className="absolute inset-0 rounded-full border-2 border-purple-400/50"
            initial={{ opacity: 0, scale: 0.8 }}
            animate={{ opacity: 1, scale: 1, rotate: 360 }}
            exit={{ opacity: 0, scale: 0.8 }}
            transition={{
              rotate: { duration: 4, repeat: Infinity, ease: "linear" },
            }}
          />
        )}
      </AnimatePresence>
    </motion.div>
  );
};
