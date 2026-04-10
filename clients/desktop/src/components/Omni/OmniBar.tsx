/**
 * Omni-Bar — Raycast 风格悬浮条：Jachin Core + 输入 + 流式面板 + HITL
 */

import React, { useRef, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Send, Mic, Square, Radio, LayoutDashboard, Settings2 } from "lucide-react";
import { WindowControls } from "../Chat/WindowControls";
import { AssistantMessageContent } from "../Chat/AssistantMessageContent";
import { VoiceWaveform, type WavePhase } from "../Chat/VoiceWaveform";
import { JachinCore } from "./JachinCore";
import type { StoredMessage } from "../../utils/messageStorage";
import type { RiskLevel } from "../Chat/ChatUI";
import type { CoreVisualState, ToolFlashKind } from "../../hooks/useJachinCoreState";
import type { SensoryPayload } from "../../hooks/useSensoryWebSocket";
import type { ToolUiSubmitPayload } from "../../skills-ui/types";

export interface OmniBarProps {
  messages: StoredMessage[];
  input: string;
  onInputChange: (value: string) => void;
  onSend: () => void;
  onVoiceStart: () => void;
  onVoiceStop: () => void;
  isVadActive?: boolean;
  onVadToggle?: () => void;
  isLoading: boolean;
  isTyping: boolean;
  isRecording: boolean;
  recordingStatus: string;
  listeningText?: string;
  placeholder?: string;
  riskLevel?: RiskLevel;
  disabled?: boolean;
  streamingFromWs?: boolean;
  interactionPhase?: "text" | WavePhase;
  micLevel?: number;
  onOpenConsole?: () => void;
  /** 流光核心 */
  coreState: CoreVisualState;
  toolFlash: ToolFlashKind;
  streamingContent: string;
  /** HITL */
  hitlPending: SensoryPayload | null;
  onHitlResolve: (approved: boolean) => void;
  onToolUiResult?: (payload: ToolUiSubmitPayload) => void;
}

const displayMessages = (messages: StoredMessage[]) =>
  messages.filter((m) => m.role === "user" || m.role === "assistant");

export const OmniBar: React.FC<OmniBarProps> = ({
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
  placeholder = "输入指令…",
  riskLevel = "safe",
  disabled = false,
  streamingFromWs = false,
  interactionPhase = "text",
  micLevel = 0,
  onOpenConsole,
  coreState,
  toolFlash,
  streamingContent,
  hitlPending,
  onHitlResolve,
  onToolUiResult,
}) => {
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isTyping, streamingContent]);

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

  const showStreamPanel =
    !hitlPending &&
    (streamingContent.length > 0 || (streamingFromWs && isTyping));

  return (
    <div
      className={`w-full h-full min-h-0 flex flex-col rounded-2xl overflow-hidden pointer-events-none ${riskBorder}`}
      style={{ background: "transparent" }}
    >
      {/* mt-auto：玻璃面板按内容高度，整体贴窗口底部，避免 flex-1 在 100vh 下产生大块空白 */}
      <div className="relative flex flex-col mt-auto w-full shrink-0 pointer-events-auto rounded-2xl">
        <div
          className="absolute inset-0 pointer-events-none rounded-2xl"
          style={{
            backgroundColor: "rgba(6, 14, 32, 0.5)",
            backdropFilter: "blur(20px) saturate(1.15)",
            WebkitBackdropFilter: "blur(20px) saturate(1.15)",
          }}
        />
        <div className="absolute inset-0 pointer-events-none rounded-2xl shadow-[inset_0_0_48px_rgba(34,211,238,0.06)]" />

        <div
          className="h-6 flex-shrink-0 flex items-center justify-center cursor-grab active:cursor-grabbing select-none relative z-10"
          data-tauri-drag-region
        >
          <span className="w-7 h-0.5 rounded-full bg-cyan-400/35" />
        </div>

        <header className="flex justify-between items-center px-3 pb-1 pt-0 flex-shrink-0 relative z-10">
          <span className="text-[10px] uppercase tracking-[0.2em] text-cyan-400/80">Jachin Omni</span>
          <div className="flex items-center gap-0.5">
            <WindowControls />
          </div>
        </header>

        {/* 极简上下文：仅少量最近气泡 */}
        {list.length > 0 && (
          <div className="max-h-[30vh] overflow-y-auto space-y-2 px-3 text-xs text-cyan-50/90 scrollbar-thin relative z-10 shrink-0">
            {list.slice(-6).map((msg, idx) => (
              <div
                key={`${msg.timestamp}-${idx}`}
                className={`rounded-lg px-2 py-1.5 ${
                  msg.role === "user" ? "bg-cyan-500/15 ml-4" : "bg-white/5 mr-4"
                }`}
              >
                {msg.role === "assistant" ? (
                  <AssistantMessageContent
                    message={msg}
                    isLastAssistant={idx === list.length - 1}
                    isTyping={isTyping}
                    variant="markdown"
                    streamingFromWs={streamingFromWs}
                    onToolUiResult={onToolUiResult}
                  />
                ) : (
                  msg.content
                )}
              </div>
            ))}
            <div ref={messagesEndRef} />
          </div>
        )}

        <AnimatePresence>
          {hitlPending && (
            <motion.div
              key="hitl-panel"
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              className="overflow-hidden border-t border-red-500/30 relative z-20"
            >
              <div className="p-3 space-y-3 bg-red-950/40">
                <p className="text-center text-red-300 text-xs font-semibold tracking-wide">HITL · 需人工授权</p>
                <p className="text-slate-300 text-xs font-mono max-h-24 overflow-y-auto whitespace-pre-wrap">
                  {hitlPending.content || "[高危操作待确认]"}
                </p>
                <div className="flex gap-2 justify-center">
                  <button
                    type="button"
                    className="px-4 py-2 rounded-lg bg-emerald-600/80 text-white text-sm font-medium hover:bg-emerald-500"
                    onClick={() => onHitlResolve(true)}
                  >
                    授权通过
                  </button>
                  <button
                    type="button"
                    className="px-4 py-2 rounded-lg bg-slate-700 text-slate-100 text-sm hover:bg-slate-600"
                    onClick={() => onHitlResolve(false)}
                  >
                    拦截销毁
                  </button>
                </div>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        <AnimatePresence>
          {showStreamPanel && (
            <motion.div
              key="stream"
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.28, ease: "easeOut" }}
              className="overflow-hidden border-t border-white/10 relative z-10"
            >
              <motion.div
                className="h-1 w-full bg-gradient-to-r from-cyan-500/50 via-violet-500/60 to-cyan-500/50"
                animate={{ backgroundPosition: ["0% 50%", "100% 50%"] }}
                transition={{ duration: 2, repeat: Infinity, ease: "linear" }}
                style={{ backgroundSize: "200% 100%" }}
              />
              <div className="p-3 max-h-40 overflow-y-auto text-sm text-cyan-50/95 font-mono leading-relaxed scrollbar-thin">
                {streamingContent ||
                  (streamingFromWs && isTyping ? "…" : null)}
                {streamingFromWs && isTyping && (
                  <span className="inline-block w-1.5 h-3 ml-0.5 bg-cyan-400/90 animate-pulse align-middle" />
                )}
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        {(recordingStatus || listeningText || isVadActive) && (
          <div className="flex flex-col gap-1 mx-3 mb-1 flex-shrink-0 text-[10px] relative z-10">
            {isVadActive && (
              <div className="text-amber-300/90 px-2 py-0.5 rounded bg-amber-500/10 border border-amber-500/25">
                VAD 监听中…
              </div>
            )}
            {recordingStatus && (
              <div
                className={
                  recordingStatus.includes("错误") ? "text-red-300" : "text-cyan-300/90"
                }
              >
                {recordingStatus}
              </div>
            )}
          </div>
        )}

        {/* 勿在容器上对 mousedown/pointerdown 调用 preventDefault，否则从 input 冒泡后会取消默认聚焦，WebView 内表现为无法输入 */}
        <div
          data-chat-interactive
          className="flex items-center gap-2 px-3 pb-3 pt-1 flex-shrink-0 relative z-20"
          style={{ pointerEvents: "auto" }}
        >
          <JachinCore state={coreState} toolFlash={toolFlash} />

          {onVadToggle != null && (
            <button
              type="button"
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                onVadToggle();
              }}
              disabled={disabled || isLoading}
              className={`px-2 py-2 rounded-full border flex-shrink-0 flex items-center gap-1 ${
                isVadActive
                  ? "bg-amber-500/25 text-amber-300 border-amber-500/40"
                  : "bg-white/10 border-white/20 text-slate-400"
              }`}
            >
              <Radio className="w-4 h-4" />
              <span className="text-[10px] font-medium uppercase">VAD</span>
            </button>
          )}

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
            onMouseLeave={() => isRecording && onVoiceStop()}
            disabled={!canVoice}
            className={`p-2.5 rounded-full border flex-shrink-0 ${
              isRecording
                ? "bg-red-500/25 text-red-300 border-red-500/40"
                : "bg-white/10 border-cyan-400/30 text-cyan-400"
            }`}
          >
            {isRecording ? <Square className="w-4 h-4" /> : <Mic className="w-4 h-4" />}
          </button>

          <div className="flex-1 min-w-0 flex flex-col justify-center">
            {voiceVisual ? (
              <VoiceWaveform phase={wavePhase} micLevel={micLevel} />
            ) : (
              <div className="relative group min-w-0">
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
                  placeholder={placeholder}
                  readOnly={isLoading}
                  disabled={disabled}
                  autoComplete="off"
                  spellCheck={false}
                  className="w-full bg-transparent border-none py-2 pl-2 pr-2 text-sm text-cyan-100 placeholder-cyan-400/50 focus:outline-none disabled:opacity-50"
                />
                <div className="absolute bottom-0 left-0 right-0 h-px bg-white/10" />
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
              disabled={!canSend}
              className="p-2 rounded-full text-cyan-400 border border-cyan-500/30 hover:bg-cyan-500/20 disabled:opacity-40"
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
              title="大控制台"
              className="p-2 rounded-full text-slate-400 border border-white/10 hover:text-cyan-300"
            >
              <LayoutDashboard className="w-4 h-4" />
            </button>
          )}
          <button
            type="button"
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              onOpenConsole?.();
            }}
            title="设置（控制台）"
            className="p-2 rounded-full text-slate-500 border border-white/10 hover:text-cyan-300"
          >
            <Settings2 className="w-4 h-4" />
          </button>
        </div>
      </div>
    </div>
  );
};

export default OmniBar;
