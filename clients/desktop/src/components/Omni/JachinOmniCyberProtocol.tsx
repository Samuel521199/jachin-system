/**
 * Omni 赛博协议壳层 — 对话历史 + 底栏胶囊；思考过程与正文隔离展示
 */

import React, { useRef, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Send,
  Mic,
  Square,
  Radio,
  LayoutDashboard,
  Settings2,
  Plus,
  Menu,
  Trash2,
  FileText,
  Image as ImageIcon,
  X,
} from "lucide-react";
import type { StoredMessage } from "../../utils/messageStorage";
import { AssistantMessageContent } from "../Chat/AssistantMessageContent";
import type { ToolUiSubmitPayload } from "../../skills-ui/types";
import { WindowControls } from "../Chat/WindowControls";
import { VoiceWaveform, type WavePhase } from "../Chat/VoiceWaveform";
import { JachinCore, type JachinCoreMachineState } from "./JachinCore";
import { OmniReasoningChain } from "./OmniReasoningChain";
import type { RiskLevel } from "../Chat/ChatUI";
import type { CoreVisualState, ToolFlashKind } from "../../hooks/useJachinCoreState";
import type { SensoryPayload } from "../../hooks/useSensoryWebSocket";
import { desktopOmniUi, type DesktopOmniUiStrings } from "../../utils/desktopUiI18n";
import { getAssistantReasoningForDisplay } from "../../utils/reasoningStreamSplit";
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
  /** 思考/流式过程中停止生成（与发送按钮同位） */
  onStopGeneration?: () => void;
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
  /** 生成式 UI：用户提交工具结果时回调（可选，未传则面板内仅 console 警告） */
  onToolUiResult?: (payload: ToolUiSubmitPayload) => void;
  /** 仅开发构建：标题栏可折叠插槽（如 tool_call 演示注入） */
  devToolbar?: React.ReactNode;
  /** 新建会话（侧栏「发起新对话」） */
  onNewChat?: () => void;
  /** 左侧会话列表抽屉 */
  sessionDrawerOpen?: boolean;
  onToggleSessionDrawer?: () => void;
  sessionsList?: { id: string; title: string }[];
  currentSessionId?: string | null;
  onSelectSession?: (id: string) => void;
  onDeleteSession?: (id: string) => void;
  /** 待发送附件（多模态）；与 `onMergePendingFiles` / `onRemovePendingFile` 成组使用 */
  pendingFiles?: File[];
  /** 合并新选/拖入的文件（父级做体积与类型校验） */
  onMergePendingFiles?: (files: File[]) => void;
  onRemovePendingFile?: (index: number) => void;
  /** 隐藏 file input 的 ref，供父级聚焦（可选） */
  fileInputRef?: React.RefObject<HTMLInputElement | null>;
  /** 附件提示条（校验失败文案） */
  attachmentHint?: string | null;
  /** 是否显示全局拖拽高亮（由父级控制） */
  dragOverlayActive?: boolean;
  /** 与 Horizon 语言菜单联动（默认中文） */
  ui?: DesktopOmniUiStrings;
}

export const OmniCyberChatShell: React.FC<OmniCyberChatShellProps> = ({
  phase,
  jachinMachineState,
  thinkingToolFlash,
  input,
  onInputChange,
  onRequestDismiss,
  onSend,
  onStopGeneration,
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
  onToolUiResult,
  devToolbar,
  onNewChat,
  sessionDrawerOpen = false,
  onToggleSessionDrawer,
  sessionsList = [],
  currentSessionId = null,
  onSelectSession,
  onDeleteSession,
  pendingFiles = [],
  onMergePendingFiles,
  onRemovePendingFile,
  fileInputRef: fileInputRefProp,
  attachmentHint = null,
  dragOverlayActive = false,
  ui = desktopOmniUi.zh,
}) => {
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const fileInputLocalRef = useRef<HTMLInputElement>(null);
  const fileInputRef = fileInputRefProp ?? fileInputLocalRef;

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isTyping]);

  const hasPendingFiles = pendingFiles.length > 0;
  const canSend = !disabled && !isLoading && (input.trim().length > 0 || hasPendingFiles);
  const stopMode =
    onStopGeneration != null &&
    (jachinMachineState === "THINKING" || jachinMachineState === "STREAMING");
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
      className={`relative flex h-full min-h-0 w-full flex-col overflow-hidden rounded-2xl pointer-events-none ${riskBorder}`}
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
          <AnimatePresence>
            {sessionDrawerOpen && onToggleSessionDrawer != null && (
              <>
                <motion.button
                  type="button"
                  key="session-drawer-scrim"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.2 }}
                  className="absolute inset-0 z-[25] bg-black/50"
                  aria-label={ui.closeSessionList}
                  onClick={() => onToggleSessionDrawer()}
                />
                <motion.aside
                  key="session-drawer"
                  initial={{ x: "-100%" }}
                  animate={{ x: 0 }}
                  exit={{ x: "-100%" }}
                  transition={{ type: "spring", stiffness: 380, damping: 34 }}
                  className="absolute left-0 top-0 z-[30] flex h-full w-[min(280px,85vw)] min-h-0 flex-col border-r border-white/10 bg-black/80 shadow-[4px_0_32px_rgba(0,0,0,0.55)] backdrop-blur-xl"
                >
                  <div className="shrink-0 border-b border-white/10 p-3">
                    {onNewChat != null ? (
                      <button
                        type="button"
                        onClick={(e) => {
                          e.preventDefault();
                          e.stopPropagation();
                          onNewChat();
                        }}
                        className="flex w-full items-center justify-center gap-2 rounded-lg border border-cyan-500/40 bg-cyan-500/15 py-2.5 text-center font-mono text-[11px] font-medium text-cyan-100 shadow-[0_0_20px_rgba(34,211,238,0.12)] transition-[box-shadow,background-color] hover:border-cyan-400/55 hover:bg-cyan-500/25"
                      >
                        <Plus className="h-3.5 w-3.5 shrink-0" strokeWidth={2.4} />
                        {ui.newChatSidebar}
                      </button>
                    ) : null}
                  </div>
                  <ul className="flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto px-2 py-2">
                    {sessionsList.map((s) => {
                      const active = s.id === currentSessionId;
                      return (
                        <li key={s.id} className="group flex min-w-0 items-stretch gap-0.5">
                          <button
                            type="button"
                            onClick={(e) => {
                              e.preventDefault();
                              e.stopPropagation();
                              onSelectSession?.(s.id);
                            }}
                            className={`min-w-0 flex-1 rounded-r-md border border-l-2 py-2 pl-3 pr-2 text-left font-mono text-[11px] leading-snug transition-colors ${
                              active
                                ? "border-l-cyan-400 border-y-white/10 border-r-white/10 bg-cyan-500/10 text-cyan-50"
                                : "border-l-transparent border-transparent text-slate-400 hover:border-white/10 hover:bg-white/[0.06] hover:text-slate-200"
                            }`}
                          >
                            <span className="line-clamp-2">{s.title || ui.newChatFallback}</span>
                          </button>
                          {onDeleteSession != null ? (
                            <button
                              type="button"
                              title={ui.deleteSession}
                              onClick={(e) => {
                                e.preventDefault();
                                e.stopPropagation();
                                onDeleteSession(s.id);
                              }}
                              className="flex w-8 shrink-0 items-center justify-center rounded-md text-slate-500 opacity-0 transition-opacity hover:bg-red-500/15 hover:text-red-300 group-hover:opacity-100"
                            >
                              <Trash2 className="h-3.5 w-3.5" strokeWidth={2} />
                            </button>
                          ) : null}
                        </li>
                      );
                    })}
                  </ul>
                </motion.aside>
              </>
            )}
          </AnimatePresence>
          {/* 拖拽区勿包住窗口按钮：否则 WebView2 会吞掉首次点击，× / 最小化无响应 */}
          <div className="flex shrink-0 select-none items-center justify-between gap-2 px-3 pb-1.5 pt-2">
            <div
              data-tauri-drag-region
              className="flex min-w-0 flex-1 items-center gap-2"
            >
              <span className="pointer-events-none text-[10px] font-medium uppercase tracking-[0.2em] text-cyan-400/80">
                Omni
              </span>
              {onToggleSessionDrawer != null ? (
                <button
                  type="button"
                  title={ui.sessionHistory}
                  data-tauri-drag-region="false"
                  onPointerDown={(e) => e.stopPropagation()}
                  onClick={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    onToggleSessionDrawer();
                  }}
                  className="pointer-events-auto flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-cyan-500/20 text-cyan-300/90 transition-[box-shadow,background-color,border-color] hover:border-cyan-400/45 hover:bg-cyan-500/10 hover:shadow-[0_0_14px_rgba(34,211,238,0.28)] active:scale-95"
                >
                  <Menu className="h-3.5 w-3.5" strokeWidth={2.4} />
                </button>
              ) : onNewChat != null ? (
                <button
                  type="button"
                  title={ui.newChatTitle}
                  data-tauri-drag-region="false"
                  onPointerDown={(e) => e.stopPropagation()}
                  onClick={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    onNewChat();
                  }}
                  className="pointer-events-auto flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-cyan-500/20 text-cyan-300/90 transition-[box-shadow,background-color,border-color] hover:border-cyan-400/45 hover:bg-cyan-500/10 hover:shadow-[0_0_14px_rgba(34,211,238,0.28)] active:scale-95"
                >
                  <Plus className="h-3.5 w-3.5" strokeWidth={2.4} />
                </button>
              ) : null}
              <div className="pointer-events-none flex min-w-0 flex-1 justify-center px-2">
                <span className="h-0.5 w-7 shrink-0 rounded-full bg-cyan-400/35" aria-hidden />
              </div>
            </div>
            <div
              className="flex shrink-0 items-center gap-1"
              data-tauri-drag-region="false"
              onPointerDown={(e) => e.stopPropagation()}
            >
              {devToolbar != null ? devToolbar : null}
              <WindowControls onCloseOverride={onRequestDismiss} onMinimizeOverride={onRequestDismiss} />
            </div>
          </div>

          {/* 中部可滚动：HITL / 流式 / 状态，撑满剩余高度，绝不挤出底栏（min-h-0 保证 flex 子项可收缩，否则表现为「只显示一半」） */}
          <div className="min-h-0 flex-1 overflow-x-hidden overflow-y-auto overscroll-y-contain">
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
                    <p className="text-center text-xs font-semibold tracking-wide text-red-300">{ui.hitlTitle}</p>
                    <p className="max-h-24 overflow-y-auto whitespace-pre-wrap font-mono text-xs text-slate-300">
                      {hitlPending.content || ui.hitlFallback}
                    </p>
                    <div className="flex justify-center gap-2">
                      <button
                        type="button"
                        className="rounded-lg bg-emerald-600/80 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500"
                        onClick={() => onHitlResolve(true)}
                      >
                        {ui.hitlApprove}
                      </button>
                      <button
                        type="button"
                        className="rounded-lg bg-slate-700 px-4 py-2 text-sm text-slate-100 hover:bg-slate-600"
                        onClick={() => onHitlResolve(false)}
                      >
                        {ui.hitlReject}
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
                    {ui.emptyThreadHint}
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
                    const reasoningChainText = getAssistantReasoningForDisplay(msg);
                    return (
                      <div key={`${msg.timestamp}-${idx}`} className="flex justify-start">
                        <div className="max-w-[92%] rounded-2xl border border-white/10 bg-slate-950/55 px-3 py-2 text-sm shadow-inner">
                          {!!reasoningChainText.trim() && (
                            <OmniReasoningChain
                              text={reasoningChainText}
                              isStreaming={isLastAssistant && isTyping}
                              labels={{
                                chain: ui.reasoningChain,
                                expand: ui.reasoningExpand,
                                updating: ui.reasoningUpdating,
                              }}
                            />
                          )}
                          <div className="break-words leading-relaxed text-cyan-50/95 [&_.markdown-content]:font-medium">
                            <AssistantMessageContent
                              message={msg}
                              isLastAssistant={isLastAssistant}
                              isTyping={isTyping}
                              variant="markdown"
                              onToolUiResult={onToolUiResult}
                            />
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
                    {ui.vadListening}
                  </div>
                )}
                {recordingStatus && (
                  <div
                    className={
                      /错误|error/i.test(recordingStatus) ? "text-red-300" : "text-cyan-300/90"
                    }
                  >
                    {recordingStatus}
                  </div>
                )}
              </div>
            )}
          </div>

          {/* 附件预览条（在输入条上方） */}
          {(hasPendingFiles || attachmentHint) && (
            <div className="relative z-20 shrink-0 border-t border-cyan-500/15 bg-slate-950/40 px-3 pb-2 pt-2">
              {attachmentHint ? (
                <p className="mb-2 text-[11px] text-amber-300/90">{attachmentHint}</p>
              ) : null}
              {hasPendingFiles ? (
                <div className="flex flex-wrap gap-2">
                  {pendingFiles.map((file, idx) => {
                    const isImg = file.type.startsWith("image/");
                    const url = isImg ? URL.createObjectURL(file) : "";
                    return (
                      <div
                        key={`${file.name}-${file.size}-${idx}`}
                        className="group relative flex max-w-[140px] items-center gap-2 rounded-lg border border-cyan-500/35 bg-slate-800/50 px-2 py-1.5 shadow-[0_0_12px_rgba(34,211,238,0.08)]"
                      >
                        {isImg ? (
                          <img src={url} alt="" className="h-10 w-10 shrink-0 rounded object-cover" />
                        ) : (
                          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded bg-slate-900/80 text-cyan-400/90">
                            <FileText className="h-5 w-5" />
                          </div>
                        )}
                        <div className="min-w-0 flex-1">
                          <p className="truncate font-mono text-[10px] text-cyan-100/90" title={file.name}>
                            {file.name}
                          </p>
                          <p className="font-mono text-[9px] text-slate-500">
                            {(file.size / 1024).toFixed(1)} KB
                          </p>
                        </div>
                        {onRemovePendingFile != null ? (
                          <button
                            type="button"
                            className="absolute -right-1.5 -top-1.5 flex h-5 w-5 items-center justify-center rounded-full border border-cyan-500/40 bg-slate-950 text-cyan-200 opacity-0 shadow transition hover:bg-red-950/90 hover:text-red-100 group-hover:opacity-100"
                            aria-label="移除附件"
                            onClick={(e) => {
                              e.preventDefault();
                              e.stopPropagation();
                              onRemovePendingFile(idx);
                            }}
                          >
                            <X className="h-3 w-3" />
                          </button>
                        ) : null}
                      </div>
                    );
                  })}
                </div>
              ) : null}
            </div>
          )}

          {/* Omni-Bar：永远贴底，禁止被压缩 */}
          <div
            data-chat-interactive
            className="relative z-20 flex shrink-0 items-center gap-2 px-3 pb-3 pt-1"
            style={{ pointerEvents: "auto" }}
          >
          <input
            ref={fileInputRef as React.RefObject<HTMLInputElement>}
            type="file"
            multiple
            accept="image/*,.pdf,.doc,.docx,.xlsx,.xls,.txt"
            className="hidden"
            onChange={(e) => {
              const fl = e.target.files;
              if (fl?.length && onMergePendingFiles) onMergePendingFiles(Array.from(fl));
              e.target.value = "";
            }}
          />
          <JachinCore state={coreState} machineState={jachinMachineState} toolFlash={thinkingToolFlash} />

          {onMergePendingFiles != null && (
            <button
              type="button"
              title="添加附件"
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                fileInputRef.current?.click();
              }}
              disabled={disabled || isLoading}
              className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full border border-cyan-500/35 bg-slate-900/60 text-cyan-300 transition hover:border-cyan-400/55 hover:bg-cyan-500/10 disabled:opacity-40"
            >
              <Plus className="h-5 w-5" strokeWidth={2.25} />
            </button>
          )}

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

          {!voiceVisual &&
            (stopMode ? (
              <button
                type="button"
                onMouseDown={(e) => {
                  if (e.button !== 0) return;
                  e.preventDefault();
                  e.stopPropagation();
                  onStopGeneration?.();
                }}
                title={ui.stopGeneration}
                className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full border border-white/5 bg-white/5 text-purple-400/80 transition-all duration-300 hover:bg-purple-500/20 hover:text-white hover:shadow-[0_0_15px_rgba(168,85,247,0.3)]"
              >
                <Square className="h-4 w-4 fill-current" strokeWidth={2.25} />
              </button>
            ) : (
              <button
                type="button"
                onMouseDown={(e) => {
                  if (e.button !== 0) return;
                  e.preventDefault();
                  e.stopPropagation();
                  if (canSend) onSend();
                }}
                disabled={!canSend}
                className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full border border-cyan-500/30 text-cyan-400 transition-all duration-300 hover:bg-cyan-500/20 disabled:opacity-40"
              >
                <Send className="h-4 w-4" />
              </button>
            ))}

          {onOpenConsole && (
            <button
              type="button"
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                onOpenConsole();
              }}
              title={ui.largeConsole}
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
            title={ui.settingsConsole}
            className="p-2 rounded-full text-slate-500 border border-white/10 hover:text-cyan-300"
          >
            <Settings2 className="w-4 h-4" />
          </button>
        </div>
        </div>
      </div>

      {dragOverlayActive ? (
        <div
          className="pointer-events-none absolute inset-0 z-[60] flex items-center justify-center rounded-2xl border-2 border-dashed border-cyan-400/50 bg-cyan-500/10 backdrop-blur-[2px]"
          aria-hidden
        >
          <div className="rounded-xl border border-cyan-400/40 bg-slate-950/80 px-6 py-4 text-center shadow-[0_0_32px_rgba(34,211,238,0.2)]">
            <ImageIcon className="mx-auto mb-2 h-10 w-10 text-cyan-300/90" />
            <p className="font-mono text-sm font-medium text-cyan-100">松开以添加至会话</p>
            <p className="mt-1 font-mono text-[10px] text-cyan-400/70">图片 / PDF / Word / Excel / TXT · 最多 5 个 · 单文件 ≤ 5MB</p>
          </div>
        </div>
      ) : null}
    </div>
  );
};
