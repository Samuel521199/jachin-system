/**
 * WindowControls - Cyber-Heart 风格的自定义窗口控制按钮
 */

import React from "react";
import { Minus, X } from "lucide-react";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { motion } from "framer-motion";

export interface WindowControlsProps {
  /**
   * Omni 等：关闭改为坍缩到右下角微粒（invoke hide_chat_window），而不是直接 hide 丢陪伴态。
   * 未传时保持原行为：隐藏当前窗口。
   */
  onCloseOverride?: () => void | Promise<void>;
}

export const WindowControls: React.FC<WindowControlsProps> = ({ onCloseOverride }) => {
  const handleMinimize = async () => {
    try {
      const window = getCurrentWindow();
      await window.minimize();
    } catch (error) {
      console.error("Failed to minimize window:", error);
    }
  };

  const handleClose = async () => {
    try {
      if (onCloseOverride) {
        await Promise.resolve(onCloseOverride());
        return;
      }
      const window = getCurrentWindow();
      await window.hide();
    } catch (error) {
      console.error("Failed to close window:", error);
    }
  };

  return (
    <div className="flex items-center gap-2" data-tauri-drag-region="false">
      <motion.button
        type="button"
        data-tauri-drag-region="false"
        onClick={() => void handleMinimize()}
        className="w-6 h-6 flex items-center justify-center rounded hover:bg-white/10 transition-colors group"
        whileHover={{ scale: 1.1 }}
        whileTap={{ scale: 0.9 }}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <Minus className="w-4 h-4 text-slate-300 group-hover:text-white" />
      </motion.button>
      <motion.button
        type="button"
        data-tauri-drag-region="false"
        onClick={() => void handleClose()}
        className="w-6 h-6 flex items-center justify-center rounded hover:bg-red-500/20 transition-colors group"
        whileHover={{ scale: 1.1 }}
        whileTap={{ scale: 0.9 }}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <X className="w-4 h-4 text-slate-300 group-hover:text-red-400" />
      </motion.button>
    </div>
  );
};
