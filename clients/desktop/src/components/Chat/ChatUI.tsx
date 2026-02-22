/**
 * ChatUI — 独立 Chat 窗口用全息风格 UI（单文件、结构清晰，保证输入/按钮可响应）
 *
 * 结构：根 pointer-events-none → 面板 pointer-events-auto
 * - 标题栏：仅左侧 MIND STREAM 区域 data-tauri-drag-region
 * - 消息区：可滚动气泡
 * - 输入区：独立一层 pointer-events:auto，标准 onClick/onChange
 */

import React, { useRef, useEffect } from "react";
import { Send, Mic, Sparkles, Loader2, Square } from "lucide-react";
import { WindowControls } from "./WindowControls";
import type { StoredMessage } from "../../utils/messageStorage";

export type RiskLevel = "safe" | "warning" | "danger";

export interface ChatUIProps {
  messages: StoredMessage[];
  input: string;
  onInputChange: (value: string) => void;
  onSend: () => void;
  onVoiceStart: () => void;
  onVoiceStop: () => void;
  isLoading: boolean;
  isTyping: boolean;
  isRecording: boolean;
  recordingStatus: string;
  /** 录音过程中流式语音识别出的文字（实时转写） */
  listeningText?: string;
  placeholder?: string;
  riskLevel?: RiskLevel;
  disabled?: boolean;
}

const displayMessages = (messages: StoredMessage[]) =>
  messages.filter((m) => m.role === "user" || m.role === "assistant");

export const ChatUI: React.FC<ChatUIProps> = ({
  messages,
  input,
  onInputChange,
  onSend,
  onVoiceStart,
  onVoiceStop,
  isLoading,
  isTyping,
  isRecording,
  recordingStatus,
  listeningText = "",
  placeholder = "输入指令...",
  riskLevel = "safe",
  disabled = false,
}) => {
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isTyping]);

  const list = displayMessages(messages);
  const canSend = !disabled && !isLoading && input.trim().length > 0;
  const canVoice = !disabled && !isLoading;

  const riskBorder =
    riskLevel === "danger"
      ? "border-2 border-red-500/70"
      : riskLevel === "warning"
        ? "border-2 border-amber-500/60"
        : "";

  return (
    <div
      className={`w-full h-full min-h-0 flex flex-col rounded-2xl overflow-hidden pointer-events-none ${riskBorder}`}
      style={{ background: "transparent" }}
    >
      {/* 面板：唯一可交互层，不设 drag-region 在整块上 */}
      <div className="relative flex flex-col flex-1 min-h-0 pointer-events-auto rounded-2xl">
        {/* 装饰层：全部 pointer-events-none */}
        <div
          className="absolute inset-0 pointer-events-none rounded-2xl"
          style={{
            backgroundColor: "rgba(6, 14, 32, 0.42)",
            backdropFilter: "blur(20px) saturate(1.1)",
            WebkitBackdropFilter: "blur(20px) saturate(1.1)",
          }}
        />
        <div className="absolute inset-0 pointer-events-none rounded-2xl shadow-[inset_0_0_60px_rgba(34,211,238,0.08)]" />
        <div className="absolute top-0 left-0 w-5 h-5 border-t-2 border-l-2 border-cyan-400 pointer-events-none rounded-tl-2xl" />
        <div className="absolute top-0 right-0 w-5 h-5 border-t-2 border-r-2 border-cyan-400 pointer-events-none rounded-tr-2xl" />
        <div className="absolute bottom-0 left-0 w-5 h-5 border-b-2 border-l-2 border-violet-400/90 pointer-events-none rounded-bl-2xl" />
        <div className="absolute bottom-0 right-0 w-5 h-5 border-b-2 border-r-2 border-violet-400/90 pointer-events-none rounded-br-2xl" />
        <div className="absolute top-0 left-0 right-0 h-px bg-gradient-to-r from-transparent via-cyan-400/70 to-transparent pointer-events-none" />
        <div className="absolute bottom-0 left-0 right-0 h-px bg-gradient-to-r from-transparent via-violet-400/60 to-transparent pointer-events-none" />

        {/* 标题栏：仅左侧为拖拽区 */}
        <header className="flex justify-between items-center border-b border-white/10 pb-2 px-4 pt-4 flex-shrink-0">
          <div
            className="flex items-center gap-2 flex-1 min-w-0 cursor-grab active:cursor-grabbing"
            data-tauri-drag-region
          >
            <Sparkles className="w-4 h-4 text-cyan-400 animate-pulse flex-shrink-0" />
            <span className="font-bold tracking-widest text-sm text-cyan-100 uppercase truncate">
              MIND STREAM
            </span>
          </div>
          <div className="flex items-center gap-1 flex-shrink-0">
            <WindowControls />
          </div>
        </header>

        {/* 消息列表 */}
        <div className="flex-1 overflow-y-auto space-y-3 px-4 pr-2 min-h-0 flex-shrink text-cyan-50/95 scrollbar-thin scrollbar-thumb-cyan-500/30 scrollbar-track-transparent">
          {list.map((msg, idx) => (
            <div
              key={`${msg.timestamp}-${idx}`}
              className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
            >
              <div
                className={`max-w-[85%] p-3 rounded-2xl text-sm leading-relaxed ${
                  msg.role === "user"
                    ? "rounded-tr-md bg-gradient-to-br from-cyan-500/25 to-blue-600/20 border border-cyan-400/20"
                    : "rounded-tl-md bg-gradient-to-br from-white/8 to-slate-500/10 border border-white/10"
                }`}
              >
                <span>
                  {msg.content}
                  {isTyping && idx === list.length - 1 && msg.role === "assistant" && (
                    <span className="inline-block w-2 h-4 ml-1 bg-cyan-400/80 animate-pulse rounded-sm" />
                  )}
                </span>
              </div>
            </div>
          ))}
          {isLoading && list.length > 0 && list[list.length - 1]?.role !== "assistant" && (
            <div className="flex justify-start">
              <div className="px-3 py-2 rounded-2xl bg-white/5 border border-white/10 flex items-center gap-2">
                <Loader2 className="w-3.5 h-3.5 text-cyan-400 animate-spin" />
                <span className="text-xs text-slate-400">处理中...</span>
              </div>
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>

        {(recordingStatus || listeningText) && (
          <div className="flex flex-col gap-1 mx-4 mb-1 flex-shrink-0">
            {recordingStatus && (
              <div
                className={`text-xs px-2 py-1 rounded ${
                  recordingStatus.includes("错误") ? "bg-red-500/20 text-red-300" : "bg-cyan-500/15 text-cyan-300"
                }`}
              >
                {recordingStatus}
              </div>
            )}
            {isRecording && listeningText && (
              <div className="text-xs px-2 py-1.5 rounded bg-white/10 text-cyan-100/90 border border-cyan-400/20">
                正在听：{listeningText}
              </div>
            )}
          </div>
        )}

        {/* 输入区：用 mousedown + preventDefault 在透明窗口下抢到左键，避免点击穿透 */}
        <div
          data-chat-interactive
          className="flex items-center gap-2 p-4 pt-2 pb-4 flex-shrink-0 relative z-20"
          style={{ pointerEvents: "auto" }}
          onMouseDown={(e) => {
            e.preventDefault();
            e.stopPropagation();
          }}
          onPointerDown={(e) => {
            e.preventDefault();
            e.stopPropagation();
          }}
        >
          <button
            type="button"
            onMouseDown={(e) => {
              e.preventDefault();
              e.stopPropagation();
            }}
            onPointerDown={(e) => {
              e.preventDefault();
              e.stopPropagation();
            }}
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              if (canVoice) (isRecording ? onVoiceStop : onVoiceStart)();
            }}
            disabled={!canVoice}
            aria-label={isRecording ? "停止录音" : "语音输入"}
            className={`p-2.5 rounded-full border transition-all flex-shrink-0 cursor-pointer select-none ${
              isRecording
                ? "bg-red-500/25 text-red-300 border-red-500/40"
                : "bg-white/10 border-cyan-400/20 text-cyan-400 hover:bg-white/15 hover:border-cyan-400/40 disabled:opacity-50"
            }`}
          >
            {isRecording ? <Square className="w-4 h-4" /> : <Mic className="w-4 h-4" />}
          </button>
          <div
            className="flex-1 relative group min-w-0 cursor-text"
            style={{ pointerEvents: "auto" }}
            onMouseDown={(e) => {
              e.preventDefault();
              e.stopPropagation();
              inputRef.current?.focus();
            }}
            onPointerDown={(e) => {
              e.preventDefault();
              e.stopPropagation();
              inputRef.current?.focus();
            }}
          >
            <input
              ref={inputRef}
              type="text"
              value={input}
              onChange={(e) => onInputChange(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  if (canSend) onSend();
                }
              }}
              onMouseDown={(e) => e.stopPropagation()}
              onPointerDown={(e) => e.stopPropagation()}
              placeholder={placeholder}
              readOnly={isLoading}
              aria-label="输入消息"
              className="w-full bg-transparent border-none py-2.5 pl-2 pr-2 text-sm focus:outline-none focus:ring-0 text-cyan-100 placeholder-cyan-300/70 cursor-text"
              style={{ pointerEvents: "auto" }}
            />
            <div className="absolute bottom-0 left-0 right-0 h-[2px] bg-white/10 pointer-events-none" />
            <div className="absolute bottom-0 left-0 h-[2px] w-0 bg-cyan-400 group-focus-within:w-full transition-all duration-300 pointer-events-none" />
          </div>
          <button
            type="button"
            onMouseDown={(e) => {
              if (e.button !== 0) return;
              e.preventDefault();
              e.stopPropagation();
              if (canSend) onSend();
            }}
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
            }}
            disabled={!canSend}
            aria-label="发送"
            className="p-2.5 rounded-full text-cyan-400 hover:text-cyan-200 hover:bg-cyan-500/25 border border-cyan-400/20 transition-all disabled:opacity-50 disabled:cursor-not-allowed flex-shrink-0 cursor-pointer select-none"
            style={{ pointerEvents: "auto" }}
          >
            <Send className="w-4 h-4" />
          </button>
        </div>
      </div>
    </div>
  );
};

export default ChatUI;
