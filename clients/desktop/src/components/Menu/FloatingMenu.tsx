/**
 * FloatingMenu - 浮动菜单组件
 * 
 * Aero Prism 风格的右键菜单
 * - iOS 风格的毛玻璃效果
 * - 动画展开/收起
 * - 自定义菜单项
 */

import React from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  LayoutDashboard,
  BookOpen,
  ShoppingBag,
  MessageCircle,
  Shirt,
  Settings,
  Zap,
  LogOut,
  X,
} from "lucide-react";
import { invoke } from "@tauri-apps/api/core";
import { cn } from "../../utils/cn";

const toggleConsole = () => {
  void invoke("quick_action_eagle_eye");
};

export interface MenuAction {
  id: string;
  label: string;
  icon: React.ReactNode;
  onClick: () => void;
  danger?: boolean;
}

interface FloatingMenuProps {
  isOpen: boolean;
  position: { x: number; y: number };
  actions: MenuAction[];
  onClose: () => void;
}

export const FloatingMenu: React.FC<FloatingMenuProps> = ({
  isOpen,
  position,
  actions,
  onClose,
}) => {
  const menuRef = React.useRef<HTMLDivElement>(null);

  // 点击外部关闭菜单
  React.useEffect(() => {
    if (!isOpen) return;

    const handleClickOutside = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        onClose();
      }
    };

    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [isOpen, onClose]);

  return (
    <AnimatePresence>
      {isOpen && (
        <>
          {/* 背景遮罩 */}
          <motion.div
            className="fixed inset-0 bg-black/20 backdrop-blur-sm z-40"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={onClose}
          />

          {/* 菜单面板 */}
          <motion.div
            ref={menuRef}
            className="fixed z-50 bg-white/10 backdrop-blur-2xl rounded-2xl border border-white/20 shadow-2xl overflow-hidden min-w-[200px]"
            style={{
              left: `${position.x}px`,
              top: `${position.y}px`,
            }}
            initial={{ opacity: 0, scale: 0.8, y: -10 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.8, y: -10 }}
            transition={{ duration: 0.2, ease: "easeOut" }}
            onMouseDown={(e) => e.stopPropagation()}
          >
            {/* 菜单项列表 */}
            <div className="py-2">
              {actions.map((action, index) => (
                <motion.button
                  key={action.id}
                  className={cn(
                    "w-full px-4 py-3 flex items-center gap-3 text-left text-sm transition-colors",
                    action.danger
                      ? "text-red-400 hover:bg-red-500/20"
                      : "text-white hover:bg-white/10"
                  )}
                  onClick={() => {
                    action.onClick();
                    onClose();
                  }}
                  initial={{ opacity: 0, x: -10 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: index * 0.05 }}
                  whileHover={{ x: 4 }}
                  whileTap={{ scale: 0.98 }}
                >
                  <span className="w-5 h-5 flex items-center justify-center">
                    {action.icon}
                  </span>
                  <span>{action.label}</span>
                </motion.button>
              ))}
            </div>

            {/* 分隔线 */}
            <div className="h-px bg-white/10 my-1" />

            {/* 关闭按钮 */}
            <motion.button
              className="w-full px-4 py-3 flex items-center gap-3 text-left text-sm text-gray-400 hover:bg-white/10 transition-colors"
              onClick={onClose}
              whileHover={{ x: 4 }}
              whileTap={{ scale: 0.98 }}
            >
              <span className="w-5 h-5 flex items-center justify-center">
                <X className="w-4 h-4" />
              </span>
              <span>关闭</span>
            </motion.button>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
};

// 默认菜单项
export const defaultMenuActions: MenuAction[] = [
  {
    id: "chat",
    label: "打开聊天",
    icon: <MessageCircle className="w-4 h-4" />,
    onClick: () => void invoke("show_chat_window"),
  },
  {
    id: "english-vocab",
    label: "英语背词",
    icon: <BookOpen className="w-4 h-4" />,
    onClick: () => void invoke("show_english_vocab_window"),
  },
  {
    id: "console",
    label: "显示或隐藏控制台",
    icon: <LayoutDashboard className="w-4 h-4" />,
    onClick: toggleConsole,
  },
  {
    id: "market",
    label: "Skill Market",
    icon: <ShoppingBag className="w-4 h-4" />,
    onClick: () => {
      toggleConsole();
    },
  },
  {
    id: "skin",
    label: "更换形象",
    icon: <Shirt className="w-4 h-4" />,
    onClick: () => {
      toggleConsole();
    },
  },
  {
    id: "settings",
    label: "设置",
    icon: <Settings className="w-4 h-4" />,
    onClick: toggleConsole,
  },
  {
    id: "plugins",
    label: "插件 / 技能",
    icon: <Zap className="w-4 h-4" />,
    onClick: toggleConsole,
  },
  {
    id: "quit",
    label: "退出应用",
    icon: <LogOut className="w-4 h-4" />,
    onClick: () => void invoke("app_exit"),
    danger: true,
  },
];
