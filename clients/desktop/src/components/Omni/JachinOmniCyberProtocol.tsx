/**
 * Omni 赛博协议壳层 — 对话历史 + 底栏胶囊；思考过程与正文隔离展示
 */

import React, { useRef, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Send, Mic, Square, Radio, LayoutDashboard, Settings2, Brain } from "lucide-react";
import type { StoredMessage } from "../../utils/messageStorage";
import { WindowControls } from "../Chat/WindowControls";
import { VoiceWaveform, type WavePhase } from "../Chat/VoiceWaveform";
import { JachinCore, type JachinCoreMachineState } from "./JachinCore";
import type { RiskLevel } from "../Chat/ChatUI";
import type { CoreVisualState, ToolFlashKind } from "../../hooks/useJachinCoreState";
import type { SensoryPayload } from "../../hooks/useSensoryWebSocket";

export enum CorePhase {
  IDLE = "IDLE",
  THINKING = "THINKING",
  HEALING = "HEALING",
  STREAMING = "STREAMING",
}

function phaseToCoreVisual(
  phase: CorePhase,
  hitlPending: SensoryPayload | null
): CoreVisualState {
  if (hitlPending) return "hitl";
  switch (phase) {
    case CorePhase.HEALING:
      return "self_heal";
    case CorePhase.THINKING:
      return "thinking";
    case CorePhase.STREAMING:
      return "streaming";
    default:
      return "idle";
  }
}

export interface OmniCyberChatShellProps {
  phase: CorePhase;
  /** 与气泡 Thought Process / 正文流式同步，驱动 JachinCore 呼吸环 */
  jachinMachineState: JachinCoreMachineState;
  thinkingToolFlash: ToolFlashKind;
  messages: StoredMessage[];
  input: string;
  onInputChange: (value: string) => void;
  /** Esc 收起 Omni / 陪伴圆（与 window 捕获监听双保险，避免 WebView 吞键） */
  onRequestDismiss?: () => void | Promise<void>;
  onSend: () => void;
  placeholder?: string;
  disabled?: boolean;
  isLoading: boolean;
  isTyping: boolean;
  isRecording: boolean;
  onVoiceStart: () => void;
  onVoiceStop: () => void;
  isVadActive?: boolean;
  onVadToggle?: () => void;
  interactionPhase?: "text" | WavePhase;
  micLevel?: number;
  onOpenConsole?: () => void;
  recordingStatus: string;
  listeningText?: string;
  hitlPending: SensoryPayload | null;
  onHitlResolve: (approved: boolean) => void;
  riskLevel?: RiskLevel;
}

export const OmniCyberChatShell: React.FC<OmniCyberChatShellProps> = ({
  phase,
  jachinMachineState,
  thinkingToolFlash,
  input,
  onInputChange,
  onRequestDismiss,
  onSend,
  placeholder = "输入指令…",
  disabled = false,
  isLoading,
  isTyping,
  messages,
  isRecording,
  onVoiceStart,
  onVoiceStop,
  isVadActive = false,
  onVadToggle,
  interactionPhase = "text",
  micLevel = 0,
  onOpenConsole,
  recordingStatus,
  listeningText = "",
  hitlPending,
  onHitlResolve,
  riskLevel = "safe",
}) => {
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isTyping]);

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

  const coreState = phaseToCoreVisual(phase, hitlPending);

  return (
    <div
      className={`flex h-full min-h-0 w-full flex-col overflow-hidden rounded-2xl pointer-events-none ${riskBorder}`}
      style={{ background: "transparent" }}
    >
      <div className="relative flex h-full min-h-0 w-full flex-col overflow-hidden rounded-2xl pointer-events-auto">
        <div
          className="pointer-events-none absolute inset-0 rounded-2xl"
          style={{
            backgroundColor: "rgba(6, 14, 32, 0.5)",
            backdropFilter: "blur(20px) saturate(1.15)",
            WebkitBackdropFilter: "blur(20px) saturate(1.15)",
          }}
        />
        <div className="pointer-events-none absolute inset-0 rounded-2xl shadow-[inset_0_0_48px_rgba(34,211,238,0.06)]" />

        <div className="relative z-10 flex h-full min-h-0 flex-col overflow-hidden">
          {/* 原生拖拽区：整段标题栏交给系统，避免仅细条可拖；按钮区 pointer-events-auto */}
          <div
            data-tauri-drag-region
            className="flex shrink-0 select-none items-center justify-between gap-2 px-3 pb-1.5 pt-2"
          >
            <span className="pointer-events-none text-[10px] font-medium uppercase tracking-[0.2em] text-cyan-400/80">
              Omni
            </span>
            <div className="pointer-events-none flex min-w-0 flex-1 justify-center px-2">
              <span className="h-0.5 w-7 shrink-0 rounded-full bg-cyan-400/35" aria-hidden />
            </div>
            <div className="pointer-events-auto shrink-0">
              <WindowControls />
            </div>
          </div>

          {/* 中部可滚动：HITL / 流式 / 状态，撑满剩余高度，绝不挤出底栏 */}
          <div className="min-h-0 flex-1 overflow-x-hidden overflow-y-auto">
            <AnimatePresence>
              {hitlPending && (
                <motion.div
                  key="hitl-panel"
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: "auto", opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  className="relative z-20 overflow-hidden border-t border-red-500/30"
                >
                  <div className="space-y-3 bg-red-950/40 p-3">
                    <p className="text-center text-xs font-semibold tracking-wide text-red-300">HITL · 需人工授权</p>
                    <p className="max-h-24 overflow-y-auto whitespace-pre-wrap font-mono text-xs text-slate-300">
                      {hitlPending.content || "[高危操作待确认]"}
                    </p>
                    <div className="flex justify-center gap-2">
                      <button
                        type="button"
                        className="rounded-lg bg-emerald-600/80 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500"
                        onClick={() => onHitlResolve(true)}
                      >
                        授权通过
                      </button>
                      <button
                        type="button"
                        className="rounded-lg bg-slate-700 px-4 py-2 text-sm text-slate-100 hover:bg-slate-600"
                        onClick={() => onHitlResolve(false)}
                      >
                        拦截销毁
                      </button>
                    </div>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>

            {!hitlPending && (
              <div className="relative z-10 flex flex-col gap-2 px-3 py-2">
                {messages.length === 0 && (
                  <p className="text-center text-[11px] text-cyan-500/40 font-mono tracking-wide">
                    在此输入或语音，对话将按轮次显示
                  </p>
                )}
                {messages.map((msg, idx) => {
                  const isLastAssistant =
                    msg.role === "assistant" && idx === messages.length - 1;
                  if (msg.role === "user") {
                    return (
                      <div key={`${msg.timestamp}-${idx}`} className="flex justify-end">
                        <div className="max-w-[88%] rounded-2xl border border-cyan-500/25 bg-cyan-950/35 px-3 py-2 text-sm text-cyan-100/95 shadow-[0_0_20px_rgba(34,211,238,0.06)]">
                          <div className="break-words whitespace-pre-wrap leading-relaxed">{msg.content}</div>
                        </div>
                      </div>
                    );
                  }
                  if (msg.role === "assistant") {
                    return (
                      <div key={`${msg.timestamp}-${idx}`} className="flex justify-start">
                        <div className="max-w-[92%] rounded-2xl border border-white/10 bg-slate-950/55 px-3 py-2 text-sm shadow-inner">
                          {!!msg.reasoning?.trim() && (
                            <details className="mb-2 group rounded-lg border border-cyan-500/15 bg-slate-900/40">
                              <summary className="cursor-pointer select-none list-none px-2 py-1.5 flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wider text-cyan-500/50 hover:text-cyan-400/70 [&::-webkit-details-marker]:hidden">
                                <Brain className="h-3.5 w-3.5 shrink-0 text-cyan-500/60" aria-hidden />
                                <span>Thought Process</span>
                                <span className="ml-auto text-[10px] font-mono text-cyan-600/40 group-open:hidden">▸</span>
                                <span className="ml-auto hidden text-[10px] font-mono text-cyan-600/40 group-open:inline">▾</span>
                              </summary>
                              <div className="border-l-2 border-cyan-500/25 mx-2 mb-2 pl-2.5 text-[11px] leading-relaxed text-cyan-500/50 font-mono whitespace-pre-wrap break-words">
                                {msg.reasoning}
                              </div>
                            </details>
                          )}
                          <div className="break-words whitespace-pre-wrap leading-relaxed font-medium text-cyan-50/95">
                            {msg.content}
                            {isLastAssistant && isTyping && (
                              <span className="ml-0.5 inline-block h-3 w-1.5 animate-pulse bg-cyan-400/90 align-middle" />
                            )}
                          </div>
                        </div>
                      </div>
                    );
                  }
                  return null;
                })}
                <div ref={messagesEndRef} className="h-px shrink-0" />
              </div>
            )}

            {(recordingStatus || listeningText || isVadActive) && (
              <div className="relative z-10 mx-3 mb-2 flex flex-col gap-1 text-[10px]">
                {isVadActive && (
                  <div className="rounded border border-amber-500/25 bg-amber-500/10 px-2 py-0.5 text-amber-300/90">
                    VAD 监听中…
                  </div>
                )}
                {recordingStatus && (
                  <div className={recordingStatus.includes("错误") ? "text-red-300" : "text-cyan-300/90"}>
                    {recordingStatus}
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Omni-Bar：永远贴底，禁止被压缩 */}
          <div
            data-chat-interactive
            className="relative z-20 flex shrink-0 items-center gap-2 px-3 pb-3 pt-1"
            style={{ pointerEvents: "auto" }}
          >
          <JachinCore state={coreState} machineState={jachinMachineState} toolFlash={thinkingToolFlash} />

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
                  type="text"
                  value={input}
                  onChange={(e) => onInputChange(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Escape") {
                      e.preventDefault();
                      e.stopPropagation();
                      void onRequestDismiss?.();
                      return;
                    }
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
    </div>
  );
};
