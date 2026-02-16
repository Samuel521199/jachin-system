/**
 * ChatWindow - 全息风格聊天窗口（独立 chat 窗口用）
 *
 * Design: 全息面板 — bg-slate-900/40 + backdrop-blur-xl、四角 HUD 装饰、
 * 主色 Cyan / 副色 Purple、输入框底线 + 聚焦时青色光线延伸。
 */

import React, { useRef, useEffect } from "react";
import { motion } from "framer-motion";
import { Send, Mic, Sparkles, Square, LayoutDashboard, Loader2 } from "lucide-react";
import { invoke } from "@tauri-apps/api/core";
import { WindowControls } from "./WindowControls";
import { DraggableRegion } from "../DraggableRegion";
import { cn } from "../../utils/cn";

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  timestamp: number;
}

interface ChatWindowProps {
  messages: ChatMessage[];
  input: string;
  onInputChange: (value: string) => void;
  onSend: () => void;
  onVoiceStart?: () => void;
  onVoiceStop?: () => void;
  isLoading?: boolean;
  isTyping?: boolean;
  isRecording?: boolean;
  placeholder?: string;
}

export const ChatWindow: React.FC<ChatWindowProps> = ({
  messages,
  input,
  onInputChange,
  onSend,
  onVoiceStart,
  onVoiceStop,
  isLoading = false,
  isTyping = false,
  isRecording = false,
  placeholder = "Input command...",
}) => {
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isTyping]);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !isLoading) {
      e.preventDefault();
      onSend();
    }
  };

  return (
    <div
      className="relative w-full h-full overflow-hidden flex flex-col rounded-2xl"
      style={{ userSelect: "none" }}
    >
      {/* 背景：半透明 + 强模糊，透出壁纸 */}
      <div className="absolute inset-0 bg-slate-900/40 backdrop-blur-xl rounded-2xl border border-white/10 shadow-[0_0_30px_rgba(0,180,255,0.2)]" />

      {/* HUD 装饰线 */}
      <div className="absolute top-0 left-0 w-full h-1 bg-gradient-to-r from-transparent via-cyan-500/50 to-transparent rounded-t-2xl pointer-events-none" />
      <div className="absolute bottom-0 left-0 w-full h-1 bg-gradient-to-r from-transparent via-purple-500/50 to-transparent rounded-b-2xl pointer-events-none" />
      <div className="absolute top-2 left-2 w-2 h-2 border-t border-l border-cyan-400 pointer-events-none" />
      <div className="absolute top-2 right-2 w-2 h-2 border-t border-r border-cyan-400 pointer-events-none" />
      <div className="absolute bottom-2 left-2 w-2 h-2 border-b border-l border-purple-400 pointer-events-none" />
      <div className="absolute bottom-2 right-2 w-2 h-2 border-b border-r border-purple-400 pointer-events-none" />

      {/* 标题栏 - 可拖动 */}
      <DraggableRegion className="relative z-10 flex items-center justify-between px-4 py-3 border-b border-white/5">
        <div className="flex items-center gap-2">
          <Sparkles className="w-4 h-4 text-cyan-400 animate-pulse" />
          <span className="font-bold tracking-wider text-sm text-cyan-100/90">MIND STREAM</span>
        </div>
        <div className="flex items-center gap-1">
          <motion.button
            type="button"
            onClick={() => void invoke("quick_action_eagle_eye")}
            className="p-1.5 rounded-md text-slate-400 hover:text-cyan-400 hover:bg-white/10 transition-colors"
            title="显示或隐藏控制台（main 界面）"
            whileHover={{ scale: 1.05 }}
            whileTap={{ scale: 0.95 }}
          >
            <LayoutDashboard className="w-4 h-4" />
          </motion.button>
          <WindowControls />
        </div>
      </DraggableRegion>

      {/* 消息区 */}
      <div className="relative z-10 flex-1 overflow-y-auto p-4 space-y-4 min-h-0">
        {messages.length === 0 ? (
          <div className="flex items-center justify-center h-full">
            <motion.div
              className="text-center"
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.2 }}
            >
              <Sparkles className="w-12 h-12 text-cyan-500/50 mx-auto mb-3" />
              <p className="text-slate-400 text-sm">开始对话吧...</p>
            </motion.div>
          </div>
        ) : (
          messages.map((message, index) => (
            <motion.div
              key={`${message.timestamp}-${index}`}
              initial={{ opacity: 0, x: message.role === "user" ? 20 : -20 }}
              animate={{ opacity: 1, x: 0 }}
              className={cn("flex", message.role === "user" ? "justify-end" : "justify-start")}
            >
              <div
                className={cn(
                  "max-w-[80%] p-3 rounded-2xl text-sm leading-relaxed relative",
                  message.role === "user"
                    ? "bg-gradient-to-br from-cyan-500/20 to-blue-600/20 text-cyan-50 border border-cyan-500/30 rounded-tr-sm"
                    : "bg-white/5 text-slate-200 border border-white/10 rounded-tl-sm"
                )}
              >
                <span className="inline-flex items-center gap-1">
                  {message.content}
                  {isTyping && index === messages.length - 1 && (
                    <span className="inline-block w-2 h-4 bg-cyan-400/80 animate-pulse" />
                  )}
                </span>
                <div className="absolute inset-0 rounded-2xl bg-gradient-to-b from-white/5 to-transparent pointer-events-none" />
              </div>
            </motion.div>
          ))
        )}
        {(isLoading || isTyping) &&
          messages.length > 0 &&
          messages[messages.length - 1]?.role !== "assistant" && (
            <div className="flex justify-start">
              <div className="p-3 rounded-2xl bg-white/5 border border-white/10 flex items-center gap-2">
                <Loader2 className="w-4 h-4 text-cyan-400 animate-spin" />
                <span className="text-sm text-slate-300">处理中...</span>
              </div>
            </div>
          )}
        <div ref={messagesEndRef} />
      </div>

      {/* 输入区：底线 + 聚焦时青色光线 */}
      <div className="relative z-20 p-4">
        <div className="flex items-center gap-2">
          <motion.button
            onClick={isRecording ? onVoiceStop : onVoiceStart}
            disabled={isLoading}
            className={cn(
              "flex items-center justify-center w-10 h-10 rounded-full border transition-all",
              isRecording
                ? "bg-red-500/30 text-red-300 border-red-500/50"
                : "bg-white/5 border-white/5 text-cyan-400/80 hover:bg-white/10"
            )}
            whileHover={!isRecording ? { scale: 1.05 } : {}}
            whileTap={{ scale: 0.95 }}
            onMouseDown={(e) => e.stopPropagation()}
          >
            {isRecording ? <Square className="w-4 h-4" /> : <Mic className="w-4 h-4" />}
          </motion.button>
          <div className="flex-1 relative group">
            <input
              ref={inputRef}
              type="text"
              value={input}
              onChange={(e) => onInputChange(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={placeholder}
              disabled={isLoading || isRecording}
              className="w-full bg-transparent border-b border-white/20 py-2.5 pl-2 pr-2 text-sm focus:outline-none focus:border-transparent text-cyan-100 placeholder-cyan-100/30 disabled:opacity-50"
              onMouseDown={(e) => e.stopPropagation()}
            />
            <div className="absolute bottom-0 left-0 h-[1px] w-0 bg-cyan-400 group-focus-within:w-full transition-all duration-500 pointer-events-none" />
          </div>
          <motion.button
            onClick={onSend}
            disabled={isLoading || !input.trim() || isRecording}
            className="p-2.5 rounded-full text-cyan-400 hover:text-cyan-200 hover:bg-cyan-500/20 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
            whileHover={!isLoading && input.trim() && !isRecording ? { scale: 1.05 } : {}}
            whileTap={{ scale: 0.95 }}
            onMouseDown={(e) => e.stopPropagation()}
          >
            {isLoading ? (
              <Loader2 className="w-5 h-5 animate-spin" />
            ) : (
              <Send className="w-5 h-5" />
            )}
          </motion.button>
        </div>
      </div>
    </div>
  );
};
