/**
 * Omni 赛博协议壳层 — 对话历史 + 底栏胶囊；思考过程与正文隔离展示
 */

import React, { useRef, useEffect, useLayoutEffect, useCallback } from "react";
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

/** 全息角标 — 四角 ⌜⌝ 细线锚定，无封闭卡片框 */
function OmniHologramCorners() {
  const c =
    "pointer-events-none absolute z-[1] h-2.5 w-2.5 border-cyan-400/80 [box-shadow:0_0_10px_rgba(34,211,238,0.25)]";
  return (
    <>
      <span className={`${c} left-0 top-0 border-l-2 border-t-2`} aria-hidden />
      <span className={`${c} right-0 top-0 border-r-2 border-t-2`} aria-hidden />
      <span className={`${c} bottom-0 left-0 border-b-2 border-l-2 border-cyan-400/60`} aria-hidden />
      <span className={`${c} bottom-0 right-0 border-b-2 border-r-2 border-cyan-400/60`} aria-hidden />
    </>
  );
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
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const adjustHeight = useCallback(() => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    const minPx = 44;
    const maxPx = 200;
    const next = Math.min(Math.max(textarea.scrollHeight, minPx), maxPx);
    textarea.style.height = `${next}px`;
  }, []);

  useLayoutEffect(() => {
    adjustHeight();
  }, [input, adjustHeight]);

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

  const clipMain =
    "[clip-path:polygon(10px_0,calc(100%-10px)_0,100%_10px,100%_calc(100%-8px),calc(100%-8px)_100%,8px_100%,0_calc(100%-8px),0_10px)]";

  return (
    <div
      className={`relative flex h-full min-h-0 w-full flex-col overflow-hidden pointer-events-none ${riskBorder} ${clipMain}`}
      style={{ background: "transparent" }}
    >
      <div className={`relative flex h-full min-h-0 w-full flex-col overflow-hidden pointer-events-auto ${clipMain}`}>
        <div
          className="pointer-events-none absolute inset-0"
          style={{
            backgroundColor: "rgba(4, 12, 24, 0.72)",
            backdropFilter: "blur(18px) saturate(1.2)",
            WebkitBackdropFilter: "blur(18px) saturate(1.2)",
          }}
        />
        <div className="pointer-events-none absolute inset-0 shadow-[inset_0_0_60px_rgba(6,182,212,0.08),0_0_40px_rgba(6,182,212,0.06)]" />

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

          {/* 中部：沉浸式消息流；HITL 时全屏雾 + 中央审批台 */}
          <div className="relative min-h-0 flex-1 overflow-x-hidden overflow-y-auto overscroll-y-contain">
            <div
              className={`relative z-10 flex min-h-full flex-col gap-3 px-4 py-3 transition-[filter,opacity] duration-300 ${
                hitlPending ? "pointer-events-none blur-[2px] brightness-[0.88] saturate-[0.85]" : ""
              }`}
            >
              {messages.length === 0 && !hitlPending && (
                <p className="py-8 text-center text-[11px] font-mono tracking-[0.28em] text-cyan-500/40 [text-shadow:0_0_12px_rgba(6,182,212,0.25)]">
                  {ui.emptyThreadHint}
                </p>
              )}
              {messages.map((msg, idx) => {
                const isLastAssistant = msg.role === "assistant" && idx === messages.length - 1;
                const tel = `0x${(((msg.timestamp ?? 0) ^ idx * 7919) & 0xffffff).toString(16).toUpperCase().padStart(6, "0")}`;
                if (msg.role === "user") {
                  return (
                    <div key={`${msg.timestamp}-${idx}`} className="flex justify-end gap-2">
                      <motion.div
                        initial={{ opacity: 0, x: 16, filter: "blur(4px)" }}
                        animate={{ opacity: 1, x: 0, filter: "blur(0px)" }}
                        transition={{ type: "spring", stiffness: 480, damping: 32 }}
                        className="relative max-w-[min(100%,42rem)] bg-cyan-950/10 px-4 py-2.5 text-sm text-cyan-50/95 shadow-[0_12px_40px_rgba(0,0,0,0.35)]"
                      >
                        <OmniHologramCorners />
                        <div className="relative z-[2] break-words whitespace-pre-wrap leading-relaxed">{msg.content}</div>
                      </motion.div>
                      <div className="flex w-8 shrink-0 flex-col items-center pt-1">
                        <div className="h-full min-h-[2rem] w-px shrink-0 omni-neural-vein shadow-[0_0_8px_rgba(34,211,238,0.35)]" />
                        <span className="mt-1 font-mono text-[7px] leading-none text-cyan-600/70">{tel}</span>
                      </div>
                    </div>
                  );
                }
                if (msg.role === "assistant") {
                  const reasoningChainText = getAssistantReasoningForDisplay(msg);
                  return (
                    <motion.div
                      key={`${msg.timestamp}-${idx}`}
                      className="flex justify-start gap-2"
                      initial={{ opacity: 0, x: -18, filter: "blur(6px)" }}
                      animate={{ opacity: 1, x: 0, filter: "blur(0px)" }}
                      transition={{ type: "spring", stiffness: 520, damping: 34 }}
                    >
                      <div className="flex w-8 shrink-0 flex-col items-center pt-1">
                        <div className="h-full min-h-[2rem] w-px shrink-0 omni-neural-vein shadow-[0_0_10px_rgba(34,211,238,0.45)]" />
                        <span className="mt-1 font-mono text-[7px] leading-none text-cyan-500/60">{tel}</span>
                      </div>
                      <div className="relative w-full max-w-[min(100%,52rem)] bg-cyan-950/10 py-2 pl-3 pr-3 text-sm shadow-[0_12px_40px_rgba(0,0,0,0.3)]">
                        <OmniHologramCorners />
                        <div className="relative z-[2]">
                          {!!reasoningChainText.trim() && (
                            <div className="mb-2 opacity-75">
                              <OmniReasoningChain
                                text={reasoningChainText}
                                isStreaming={isLastAssistant && isTyping}
                                labels={{
                                  chain: ui.reasoningChain,
                                  expand: ui.reasoningExpand,
                                  updating: ui.reasoningUpdating,
                                }}
                              />
                            </div>
                          )}
                          <div className="break-words leading-relaxed text-cyan-50/[0.93] [&_.markdown-content]:font-medium">
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
                    </motion.div>
                  );
                }
                return null;
              })}
              <div ref={messagesEndRef} className="h-px shrink-0" />
            </div>

            {(recordingStatus || listeningText || isVadActive) && (
              <div className="relative z-20 mx-4 mb-1 flex flex-col gap-1 text-[10px]">
                {isVadActive && (
                  <div className="border border-amber-500/35 bg-amber-950/30 px-2 py-1 text-amber-200/90 shadow-[0_0_16px_rgba(245,158,11,0.2)] [clip-path:polygon(6px_0,calc(100%-6px)_0,100%_6px,100%_calc(100%-6px),calc(100%-6px)_100%,6px_100%,0_calc(100%-6px),0_6px)]">
                    {ui.vadListening}
                  </div>
                )}
                {recordingStatus && (
                  <div
                    className={
                      /错误|error/i.test(recordingStatus) ? "text-red-300/95" : "text-cyan-300/85"
                    }
                  >
                    {recordingStatus}
                  </div>
                )}
              </div>
            )}

            <AnimatePresence>
              {hitlPending && (
                <>
                  <motion.div
                    key="hitl-scrim"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    transition={{ duration: 0.25 }}
                    className="pointer-events-auto absolute inset-0 z-30 bg-red-950/30 backdrop-blur-md"
                  />
                  <motion.div
                    key="hitl-console"
                    initial={{ opacity: 0, scale: 0.94, y: 8 }}
                    animate={{ opacity: 1, scale: 1, y: 0 }}
                    exit={{ opacity: 0, scale: 0.96, y: 6 }}
                    transition={{ type: "spring", stiffness: 380, damping: 28 }}
                    className="pointer-events-none absolute inset-0 z-40 flex items-center justify-center p-5"
                  >
                    <div className="pointer-events-auto w-full max-w-md border border-red-400/50 bg-black/70 p-5 shadow-[0_0_56px_rgba(239,68,68,0.38),inset_0_0_36px_rgba(239,68,68,0.07)] backdrop-blur-xl [clip-path:polygon(14px_0,calc(100%-14px)_0,100%_14px,100%_calc(100%-14px),calc(100%-14px)_100%,14px_100%,0_calc(100%-14px),0_14px)]">
                      <p className="mb-2 text-center text-[11px] font-semibold uppercase tracking-[0.28em] text-red-300/95 [text-shadow:0_0_14px_rgba(248,113,113,0.55)]">
                        {ui.hitlTitle}
                      </p>
                      <p className="mb-4 max-h-32 overflow-y-auto whitespace-pre-wrap border border-red-500/25 bg-red-950/20 px-3 py-2 font-mono text-xs leading-relaxed text-slate-200/95 shadow-[inset_0_0_20px_rgba(239,68,68,0.06)]">
                        {hitlPending.content || ui.hitlFallback}
                      </p>
                      <div className="flex justify-center gap-3">
                        <button
                          type="button"
                          className="border border-emerald-400/55 bg-emerald-950/25 px-5 py-2.5 text-sm font-medium text-emerald-200 shadow-[0_0_22px_rgba(52,211,153,0.22),inset_0_0_16px_rgba(52,211,153,0.06)] transition hover:border-emerald-300/80 hover:bg-emerald-500/15 hover:shadow-[0_0_32px_rgba(52,211,153,0.32)] [clip-path:polygon(8px_0,calc(100%-8px)_0,100%_8px,100%_calc(100%-8px),calc(100%-8px)_100%,8px_100%,0_calc(100%-8px),0_8px)]"
                          onClick={() => onHitlResolve(true)}
                        >
                          {ui.hitlApprove}
                        </button>
                        <button
                          type="button"
                          className="border border-slate-500/55 bg-slate-950/40 px-5 py-2.5 text-sm text-slate-200/95 shadow-[0_0_18px_rgba(148,163,184,0.14)] transition hover:border-slate-400/70 hover:bg-white/5 [clip-path:polygon(8px_0,calc(100%-8px)_0,100%_8px,100%_calc(100%-8px),calc(100%-8px)_100%,8px_100%,0_calc(100%-8px),0_8px)]"
                          onClick={() => onHitlResolve(false)}
                        >
                          {ui.hitlReject}
                        </button>
                      </div>
                    </div>
                  </motion.div>
                </>
              )}
            </AnimatePresence>
          </div>

          {/* 底部：外圈流星蛇仅见于 margin 环；内舱实色不透，不受锥形污染 */}
          <motion.div
            layout
            transition={{ type: "spring", stiffness: 420, damping: 32 }}
            data-chat-interactive
            className="relative z-20 mx-2 mb-4 mt-1 shrink-0"
            style={{ pointerEvents: "auto" }}
          >
            <div className="relative isolate overflow-hidden rounded-md border border-cyan-500/25 shadow-[0_20px_48px_rgba(0,0,0,0.55)]">
              <motion.div
                aria-hidden
                className="pointer-events-none absolute inset-0 z-0 will-change-transform"
                style={{
                  background:
                    "conic-gradient(from 0deg, transparent 0deg, transparent 282deg, rgba(6,182,212,0.14) 292deg, rgba(34,211,238,0.22) 300deg, rgba(45,212,191,0.38) 306deg, rgba(207,250,254,0.82) 312deg, rgba(240,249,255,0.92) 316deg, rgba(34,211,238,0.62) 322deg, rgba(6,182,212,0.28) 330deg, rgba(34,211,238,0.16) 338deg, transparent 348deg, transparent 360deg)",
                }}
                animate={{ rotate: [0, 360] }}
                transition={{ duration: 14.5, repeat: Infinity, ease: "linear" }}
              />
              <div className="relative z-[1] m-[4px] min-h-0 rounded-md bg-[#030712] px-2.5 pb-2.5 pt-2">
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

            {(hasPendingFiles || attachmentHint) && (
              <div className="mb-2 flex min-h-0 flex-wrap items-center gap-1.5 px-1">
                {attachmentHint ? (
                  <p className="w-full text-[10px] text-amber-300/90">{attachmentHint}</p>
                ) : null}
                {hasPendingFiles &&
                  pendingFiles.map((file, idx) => {
                    const isImg = file.type.startsWith("image/");
                    const url = isImg ? URL.createObjectURL(file) : "";
                    return (
                      <div
                        key={`${file.name}-${file.size}-${idx}`}
                        className="group relative inline-flex max-w-[11rem] items-center gap-1.5 bg-cyan-950/12 py-0.5 pl-2 pr-6 text-[10px] text-cyan-100/90 shadow-[0_8px_28px_rgba(0,0,0,0.35)]"
                      >
                        <OmniHologramCorners />
                        <span className="relative z-[2] flex items-center gap-1.5">
                        {isImg ? (
                          <img src={url} alt="" className="h-5 w-5 shrink-0 object-cover opacity-90" />
                        ) : (
                          <FileText className="h-3.5 w-3.5 shrink-0 text-cyan-300/80" strokeWidth={2} />
                        )}
                        <span className="truncate font-mono" title={file.name}>
                          {file.name}
                        </span>
                        {onRemovePendingFile != null ? (
                          <button
                            type="button"
                            className="absolute right-0.5 top-1/2 z-[5] flex h-5 w-5 -translate-y-1/2 items-center justify-center text-cyan-200/80 opacity-70 transition-[opacity,filter] hover:text-red-200 hover:opacity-100 hover:drop-shadow-[0_0_6px_rgba(248,113,113,0.6)]"
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
                        </span>
                      </div>
                    );
                  })}
              </div>
            )}

            <div className="flex min-h-0 w-full flex-row items-stretch gap-2">
              <div className="flex shrink-0 flex-col justify-end gap-1.5 pb-1">
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
                    className="flex h-9 w-9 shrink-0 items-center justify-center bg-transparent text-cyan-300 opacity-30 transition-[opacity,filter] duration-200 hover:opacity-100 hover:drop-shadow-[0_0_10px_rgba(34,211,238,0.95)] disabled:opacity-20"
                  >
                    <Plus className="h-4 w-4" strokeWidth={2.25} />
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
                    className={`flex h-9 w-9 shrink-0 items-center justify-center bg-transparent transition-[opacity,filter] duration-200 hover:drop-shadow-[0_0_10px_rgba(251,191,36,0.85)] disabled:opacity-20 ${
                      isVadActive
                        ? "text-amber-200 opacity-100 drop-shadow-[0_0_12px_rgba(251,191,36,0.55)]"
                        : "text-slate-500 opacity-30 hover:opacity-100"
                    }`}
                    title="VAD"
                  >
                    <Radio className="h-3.5 w-3.5 shrink-0" />
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
                  className={`flex h-9 w-9 shrink-0 items-center justify-center bg-transparent transition-[opacity,filter] duration-200 disabled:opacity-20 ${
                    isRecording
                      ? "text-red-200 opacity-100 drop-shadow-[0_0_12px_rgba(248,113,113,0.55)]"
                      : "text-cyan-300 opacity-30 hover:opacity-100 hover:drop-shadow-[0_0_10px_rgba(34,211,238,0.9)]"
                  }`}
                >
                  {isRecording ? <Square className="h-3.5 w-3.5" /> : <Mic className="h-3.5 w-3.5" />}
                </button>
              </div>

              <div className="min-w-0 flex-1 flex flex-col gap-1">
                <div className="flex items-center justify-center gap-2 pb-0.5">
                  <JachinCore
                    state={coreState}
                    machineState={jachinMachineState}
                    toolFlash={thinkingToolFlash}
                    className="!h-9 !w-9 scale-95"
                  />
                  {placeholder.includes("·") ? (
                    <span className="max-w-[min(220px,40vw)] truncate text-center text-[9px] font-mono tracking-tight text-cyan-500/40">
                      {placeholder.split("·")[0]?.trim()}
                    </span>
                  ) : null}
                </div>
                {voiceVisual ? (
                  <div className="relative flex min-h-[48px] w-full items-center justify-center bg-transparent py-2">
                    <OmniHologramCorners />
                    <div className="relative z-[2] w-full">
                      <VoiceWaveform phase={wavePhase} micLevel={micLevel} />
                    </div>
                  </div>
                ) : (
                  <div className="relative">
                    <textarea
                      ref={textareaRef}
                      rows={1}
                      value={input}
                      onChange={(e) => {
                        onInputChange(e.target.value);
                        queueMicrotask(() => adjustHeight());
                      }}
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
                      className="w-full min-h-[48px] max-h-[200px] resize-none overflow-y-auto border-0 bg-transparent px-3 py-2.5 text-sm leading-relaxed text-cyan-50/95 placeholder:text-cyan-500/40 focus:outline-none focus:ring-0 disabled:opacity-50 [scrollbar-width:none] [-ms-overflow-style:none] [&::-webkit-scrollbar]:hidden"
                    />
                  </div>
                )}
              </div>

              <div className="flex shrink-0 flex-col justify-end gap-1.5 pb-1">
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
                      className="flex h-9 w-9 shrink-0 items-center justify-center bg-transparent text-violet-300/95 opacity-100 transition-[opacity,filter] duration-200 hover:drop-shadow-[0_0_12px_rgba(167,139,250,0.85)]"
                    >
                      <Square className="h-3.5 w-3.5 fill-current" strokeWidth={2.25} />
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
                      title="发送"
                      className={`flex h-9 w-9 shrink-0 items-center justify-center bg-transparent text-cyan-200 transition-[opacity,filter] duration-200 hover:drop-shadow-[0_0_12px_rgba(34,211,238,0.95)] ${
                        canSend ? "opacity-100" : "opacity-25"
                      }`}
                    >
                      <Send className="h-3.5 w-3.5" strokeWidth={2.25} />
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
                    className="flex h-9 w-9 shrink-0 items-center justify-center bg-transparent text-slate-500 opacity-30 transition-[opacity,filter] duration-200 hover:opacity-100 hover:text-cyan-200 hover:drop-shadow-[0_0_10px_rgba(34,211,238,0.75)]"
                  >
                    <LayoutDashboard className="h-3.5 w-3.5" strokeWidth={2.25} />
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
                  className="flex h-9 w-9 shrink-0 items-center justify-center bg-transparent text-slate-500 opacity-30 transition-[opacity,filter] duration-200 hover:opacity-100 hover:text-cyan-200 hover:drop-shadow-[0_0_10px_rgba(34,211,238,0.75)]"
                >
                  <Settings2 className="h-3.5 w-3.5" strokeWidth={2.25} />
                </button>
              </div>
            </div>
              </div>
            </div>
          </motion.div>
        </div>
      </div>

      {dragOverlayActive ? (
        <div
          className="pointer-events-none absolute inset-0 z-[60] flex items-center justify-center bg-cyan-950/40 backdrop-blur-sm"
          aria-hidden
        >
          <span className="absolute left-4 top-4 h-16 w-16 border-l-[3px] border-t-[3px] border-cyan-300 shadow-[0_0_22px_rgba(34,211,238,0.65)]" />
          <span className="absolute right-4 top-4 h-16 w-16 border-r-[3px] border-t-[3px] border-cyan-300 shadow-[0_0_22px_rgba(34,211,238,0.65)]" />
          <span className="absolute bottom-4 left-4 h-16 w-16 border-b-[3px] border-l-[3px] border-cyan-300 shadow-[0_0_22px_rgba(34,211,238,0.65)]" />
          <span className="absolute bottom-4 right-4 h-16 w-16 border-b-[3px] border-r-[3px] border-cyan-300 shadow-[0_0_22px_rgba(34,211,238,0.65)]" />
          <div className="border border-cyan-400/45 bg-black/60 px-8 py-5 text-center shadow-[0_0_48px_rgba(34,211,238,0.35),inset_0_0_32px_rgba(6,182,212,0.08)] backdrop-blur-md [clip-path:polygon(16px_0,calc(100%-16px)_0,100%_16px,100%_calc(100%-16px),calc(100%-16px)_100%,16px_100%,0_calc(100%-16px),0_16px)]">
            <ImageIcon className="mx-auto mb-2 h-10 w-10 text-cyan-300 drop-shadow-[0_0_12px_rgba(34,211,238,0.6)]" />
            <p className="font-mono text-sm font-semibold tracking-wide text-cyan-100 drop-shadow-[0_0_14px_rgba(34,211,238,0.85)]">
              Drop files to upload
            </p>
            <p className="mt-1 font-mono text-[11px] text-cyan-200/75">松开以添加至会话 · 图片 / PDF / Word / Excel / TXT</p>
            <p className="mt-0.5 font-mono text-[10px] text-cyan-400/60">最多 5 个 · 单文件 ≤ 5MB</p>
          </div>
        </div>
      ) : null}
    </div>
  );
};
