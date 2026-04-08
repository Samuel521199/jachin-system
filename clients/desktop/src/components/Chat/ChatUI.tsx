/**
 * ChatUI — 独立 Chat 窗口用全息风格 UI（单文件、结构清晰，保证输入/按钮可响应）
 *
 * 结构：根 pointer-events-none → 面板 pointer-events-auto
 * - 顶部专用拖拽条：仅此区域 data-tauri-drag-region，避免与输入/话筒冲突
 * - 标题栏：MIND STREAM + 窗口按钮（不设拖拽，便于点击）
 * - 消息区：可滚动气泡
 * - 输入区：独立一层 pointer-events:auto
 */

import React, { useRef, useEffect } from "react";
import { Send, Mic, Sparkles, Loader2, Square, Radio, LayoutDashboard } from "lucide-react";
import { WindowControls } from "./WindowControls";
import { MarkdownMessage } from "./MarkdownMessage";
import { VoiceWaveform, type WavePhase } from "./VoiceWaveform";
import type { StoredMessage } from "../../utils/messageStorage";

export type RiskLevel = "safe" | "warning" | "danger";

export interface ChatUIProps {
  messages: StoredMessage[];
  input: string;
  onInputChange: (value: string) => void;
  onSend: () => void;
  /** 按键录音 PTT：按下开始、松开发送 */
  onVoiceStart: () => void;
  onVoiceStop: () => void;
  /** VAD 监听模式：开启/关闭连续监听 */
  isVadActive?: boolean;
  onVadToggle?: () => void;
  isLoading: boolean;
  isTyping: boolean;
  isRecording: boolean;
  recordingStatus: string;
  /** 录音过程中流式语音识别出的文字（实时转写） */
  listeningText?: string;
  placeholder?: string;
  riskLevel?: RiskLevel;
  disabled?: boolean;
  /** v8.0 流式神经：来自 WebSocket 的逐 token 推送，使用极客光标 █ */
  streamingFromWs?: boolean;
  /** 文本输入 vs 声波（语音/思考/播报） */
  interactionPhase?: "text" | WavePhase;
  /** 麦克风电平 0–1，用于声波条高度 */
  micLevel?: number;
  /** 打开大控制台（main） */
  onOpenConsole?: () => void;
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
  isVadActive = false,
  onVadToggle,
  isLoading,
  isTyping,
  isRecording,
  recordingStatus,
  listeningText = "",
  placeholder = "输入指令...",
  riskLevel = "safe",
  disabled = false,
  streamingFromWs = false,
  interactionPhase = "text",
  micLevel = 0,
  onOpenConsole,
}) => {
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isTyping]);

  const list = displayMessages(messages);
  const canSend = !disabled && !isLoading && input.trim().length > 0;
  const canVoice = !disabled && !isLoading;
  const voiceVisual = interactionPhase !== "text";
  const wavePhase: WavePhase = voiceVisual ? interactionPhase : "mic_listen";

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

        {/* 专用拖拽条：仅此区域可拖动窗口，与输入/话筒零冲突 */}
        <div
          className="h-7 flex-shrink-0 flex items-center justify-center cursor-grab active:cursor-grabbing select-none relative z-10"
          data-tauri-drag-region
          title="拖动窗口"
        >
          <span className="w-8 h-1 rounded-full bg-cyan-400/40" aria-hidden />
        </div>

        {/* 标题栏：不设拖拽，仅展示与窗口按钮 */}
        <header className="flex justify-between items-center border-b border-white/10 pb-2 px-4 pt-2 flex-shrink-0">
          <div className="flex items-center gap-2 flex-1 min-w-0">
            <Sparkles className="w-4 h-4 text-cyan-400 animate-pulse flex-shrink-0" />
            <span className="font-bold tracking-wide text-xs text-cyan-100 uppercase truncate">
              Jachin Omni
            </span>
            <span className="text-[10px] text-slate-500 hidden sm:inline">Alt+Shift+Space</span>
          </div>
          <div className="flex items-center gap-0.5 flex-shrink-0">
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
                <span className="contents">
                  {msg.role === "assistant" ? (
                    <>
                      <MarkdownMessage content={msg.content} />
                      {isTyping && idx === list.length - 1 && (
                        <span
                          className={streamingFromWs ? "stream-cursor" : "inline-block w-2 h-4 ml-1 bg-cyan-400/80 animate-pulse rounded-sm"}
                          aria-hidden
                        />
                      )}
                    </>
                  ) : (
                    msg.content
                  )}
                </span>
                {msg.role === "assistant" && msg.source && (
                  <span
                    className="block mt-1.5 text-[10px] text-slate-500"
                    title={msg.source === "L3" ? "Layer 3 直连大模型" : "Layer 2 兜底"}
                  >
                    via {msg.source}
                  </span>
                )}
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

        {(recordingStatus || listeningText || isVadActive) && (
          <div className="flex flex-col gap-1 mx-4 mb-1 flex-shrink-0">
            {isVadActive && (
              <div className="text-xs px-2 py-1.5 rounded bg-amber-500/15 text-amber-300 border border-amber-500/30 flex items-center gap-2">
                <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" aria-hidden />
                VAD 连续监听中… 说完即自动截断并发送
              </div>
            )}
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

        {/* 输入区：双轨 — VAD 开关 + 按键录音 Mic（按住说话） */}
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
          {/* 模式 B：VAD 连续监听 Toggle（雷达图标 + VAD 字样，开启时琥珀色高亮） */}
          {onVadToggle != null && (
            <button
              type="button"
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                onVadToggle();
              }}
              onMouseDown={(e) => e.preventDefault()}
              onPointerDown={(e) => e.stopPropagation()}
              disabled={disabled || isLoading}
              aria-label={isVadActive ? "关闭 VAD 监听" : "开启 VAD 监听"}
              title={isVadActive ? "关闭 VAD 连续监听" : "开启 VAD 连续监听（说完自动截断）"}
              className={`px-2.5 py-2 rounded-full border transition-all flex-shrink-0 cursor-pointer select-none flex items-center gap-1.5 ${
                isVadActive
                  ? "bg-amber-500/25 text-amber-300 border-amber-500/40 shadow-[0_0_12px_rgba(245,158,11,0.25)]"
                  : "bg-white/10 border-white/20 text-slate-400 hover:bg-white/15 hover:border-cyan-400/30 disabled:opacity-50"
              }`}
            >
              <Radio className="w-4 h-4 flex-shrink-0" />
              <span className="text-xs font-medium uppercase tracking-wider">VAD</span>
            </button>
          )}
          {/* 模式 A：按键录音 (PTT) — onMouseDown 开始录音，onMouseUp 结束并发送 */}
          <button
            type="button"
            onMouseDown={(e) => {
              e.preventDefault();
              e.stopPropagation();
              if (canVoice && !isRecording) onVoiceStart();
            }}
            onMouseUp={(e) => {
              e.preventDefault();
              e.stopPropagation();
              if (isRecording) onVoiceStop();
            }}
            onMouseLeave={() => {
              if (isRecording) onVoiceStop();
            }}
            onPointerDown={(e) => {
              e.preventDefault();
              e.stopPropagation();
              if (canVoice && !isRecording) onVoiceStart();
            }}
            onPointerUp={(e) => {
              e.preventDefault();
              e.stopPropagation();
              if (isRecording) onVoiceStop();
            }}
            onPointerLeave={() => {
              if (isRecording) onVoiceStop();
            }}
            disabled={!canVoice}
            aria-label={isRecording ? "松开发送" : "按住说话"}
            title={isRecording ? "松开发送" : "按住说话 (PTT)"}
            className={`p-2.5 rounded-full border transition-all flex-shrink-0 cursor-pointer select-none ${
              isRecording
                ? "bg-red-500/25 text-red-300 border-red-500/40"
                : "bg-white/10 border-cyan-400/20 text-cyan-400 hover:bg-white/15 hover:border-cyan-400/40 disabled:opacity-50"
            }`}
          >
            {isRecording ? <Square className="w-4 h-4" /> : <Mic className="w-4 h-4" />}
          </button>
          <div className="flex-1 min-w-0 flex flex-col justify-center">
            {voiceVisual ? (
              <VoiceWaveform phase={wavePhase} micLevel={micLevel} />
            ) : (
              <div
                className="relative group min-w-0 cursor-text"
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
            )}
          </div>
          {!voiceVisual && (
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
          )}
          {onOpenConsole && (
            <button
              type="button"
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                onOpenConsole();
              }}
              onMouseDown={(e) => e.stopPropagation()}
              title="大控制台（监控、日志与设置）"
              aria-label="打开大控制台"
              className="p-2 rounded-full text-slate-400 hover:text-cyan-300 hover:bg-white/10 border border-white/10 flex-shrink-0"
            >
              <LayoutDashboard className="w-4 h-4" />
            </button>
          )}
        </div>
      </div>
    </div>
  );
};

export default ChatUI;
