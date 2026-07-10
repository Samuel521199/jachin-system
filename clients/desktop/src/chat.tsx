/**
 * Chat / Omni 窗口 — Jachin Omni 极简输入条（无桌面精灵、无内嵌日志面板）
 *
 * 独立 chat 窗口入口（chat.html → 本文件）；大控制台为 `console/ConsoleApp.tsx`（main）。
 * Skill 右侧画布挂载点在本文件内联 flex 第二列，**未**使用 createPortal。
 * Sensory 步骤与回复逻辑与 `useSensoryWebSocket` + `sensoryStepFormat` 对齐（云端 v0.8.99 行为）。
 */

import React, { useState, useRef, useEffect, useLayoutEffect, useMemo, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import ReactDOM from "react-dom/client";
import { listen, emit } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { invoke } from "@tauri-apps/api/core";
import {
  appendL3VoiceDiagnostics,
  synthesizeSpeech,
  streamChatMessage,
  tryL3AgentForIntent,
  checkHealth,
} from "./lib/api";
import { useSpriteStore } from "./store/spriteStore";
import { useSttAudioReady } from "./hooks/useSttAudioReady";
import {
  useSensoryWebSocket,
  type SensoryAnswerMeta,
  type SensoryChunkMeta,
  type StreamChunkKind,
} from "./hooks/useSensoryWebSocket";
import {
  findUnresolvedToolCallMessageIndex,
  dismissUnresolvedToolCallMessage,
  StoredMessage,
} from "./utils/messageStorage";
import {
  loadSessionsState,
  persistSessionsState,
  newEmptySession,
  titleFromMessages,
  sessionSidebarDisplayLabel,
  type ChatSession,
} from "./utils/chatSessionsStore";
import { createDemoComposeEssaySkillUiMessage, createDemoGeneratePptSkillUiMessage } from "./skills-ui/devDemo";
import { getActiveSkillCanvasFromMessages, SkillCanvasPane } from "./skills-ui";
import { expandChatWindowForSkillCanvas, SKILL_CHAT_COLUMN_WIDTH } from "./skills-ui/skillCanvasWindow";
import type { ToolUiSubmitPayload } from "./skills-ui/types";
import { extractCompleteSentences, createAudioQueue } from "./utils/streamingTts";
import { typewriterAnimation } from "./utils/typewriter";
import { CHAT_RESPONSE_TIMEOUT_MS, CHAT_RESPONSE_TIMEOUT_SEC } from "./constants/chatResponseTimeout";
import {
  maybeNotifyJachinAssistantDone,
  summarizeForSentryNotify,
  type SentryNotifyVariant,
} from "./lib/jachinSentryNotify";
import { stripAssistantUiProtocol } from "./components/Chat/pendingConfirmationProtocol";
import { desktopDiagLog } from "./lib/desktopDiagLog";
import { mergeStreamChunk } from "./utils/streamChunkMerge";
import {
  armCompanionVoiceSession,
  emitCompanionL3ToHud,
  emitCompanionUserToHud,
  VOICE_COMPANION_SEND_EVENT,
  VOICE_COMPANION_TTS_EVENT,
  type VoiceCompanionTtsPayload,
} from "./voice/voiceCompanionBridge";
import { voiceOrchestrator } from "./voice/voiceOrchestrator";
import { getJvsHealth, startJvsProcess, warmJvsAudioModels } from "./voice/voiceBridge";
import {
  formatVoiceUserMessage,
  transcribeWavBase64Detailed,
  VoiceServiceError,
  type VoiceTranscriptionResult,
  VOICE_UNAVAILABLE_HINT,
} from "./voice/voiceCore";
import { VOICE_PROFILES, resolveChatSpeakSentences } from "./voice/voiceProfiles";
import {
  beginVoiceChatTrace,
  endVoiceChatTrace,
  getActiveVoiceChatTraceId,
  getVoiceTurnDiagnosticsSnapshot,
  initVoiceChatTraceLog,
  truncChatTrace,
  voiceChatTrace,
  voiceChatTraceIfActive,
  type VoiceChatUiState,
} from "./voice/voiceChatTraceLog";
import { voicePlaybackController } from "./voice/voicePlaybackController";
import { voiceSessionStore } from "./voice/voiceSessionStore";
import { initVoiceCompanionDebugLog, truncVoiceLog, voiceCompanionDebug } from "./voice/voiceCompanionDebugLog";
import { DEFAULT_KOKORO_TTS_VOICE } from "./voice/voiceDefaults";
import { l3RunIdsSameTurn } from "./utils/l3RunIdCompat";
import {
  buildAttachmentsMetadataPayload,
  mergePendingAttachmentFiles,
} from "./utils/attachmentPayload";
import {
  mergeAssistantFlatAndSplitFinalAnswer,
  normalizeAssistantOutput,
} from "./utils/reasoningStreamSplit";
import type { WavePhase } from "./components/Chat/VoiceWaveform";
import { OmniCyberChatShell, CorePhase } from "./components/Omni/JachinOmniCyberProtocol";
import { OmniDynamicHud } from "./components/Omni/OmniDynamicHud";
import { OmniTacticalVoidDecor } from "./components/Omni/OmniTacticalVoidDecor";
import type { JachinCoreMachineState } from "./components/Omni/JachinCore";
import { WindowResizeHandles } from "./components/Omni/WindowResizeHandles";
import { CompanionOverlay } from "./components/Omni/CompanionOverlay";
import type { AiState } from "./components/Omni/JachinOrb";
import { useCompanionMode } from "./hooks/useCompanionMode";

type VoiceTaskRef = {
  id: string;
  title?: string;
};
import { scheduleCompanionLayoutSyncWithRetry } from "./components/Omni/companionLayoutCheck";
import { SensoryOverlay } from "./console/components/SensoryOverlay";
import { useJachinCoreState } from "./hooks/useJachinCoreState";
import { useDesktopUiLang } from "./hooks/useDesktopUiLang";
import { getDesktopOmniUi } from "./utils/desktopUiI18n";
import "./styles/globals.css";

/** 与 Rust `HideChatWindowResult` 对齐（camelCase） */
type HideChatWindowResult = { companion: boolean; fullyHidden: boolean };

/**
 * 是否像「从资源管理器 / 桌面拖入文件」。
 * - WebView2 在 dragenter 时 `dataTransfer.types` 可能暂时为空，不能仅判断 `Files`。
 * - 页面内仅选中文本拖动通常只有 text/plain 且无 Files，不显示全窗拖放提示。
 */
function isLikelyExternalFileDrag(dt: DataTransfer | null): boolean {
  if (!dt) return false;
  const types = Array.from(dt.types ?? []);
  if (types.includes("Files")) return true;
  if (types.includes("application/x-moz-file")) return true;
  if (types.includes("text/plain") && !types.includes("Files")) return false;
  if (types.length === 0) return true;
  return false;
}

/**
 * 部分 STT/LLM 路径会在句尾稳定附带悲伤表情（如 😔），影响口语体验。
 * 这里仅移除「句尾连续悲伤 emoji」，不改正文语义。
 */
function stripDefaultSadEmojiSuffix(text: string): string {
  const t = text.trim();
  if (!t) return t;
  const cleaned = t.replace(/(?:\s*[😔😢😭☹️🙁😞])+$/u, "").trim();
  return cleaned || t;
}

function resolveCompanionJvsVoice(_voice: string): string {
  // Keep all system voice output aligned with the Kokoro trace baseline.
  return DEFAULT_KOKORO_TTS_VOICE;
}

function ChatApp() {
  const sessionBootstrap = useRef<ReturnType<typeof loadSessionsState> | null>(null);
  if (sessionBootstrap.current === null) {
    sessionBootstrap.current = loadSessionsState();
  }
  const boot = sessionBootstrap.current;

  const currentSessionIdRef = useRef(boot.currentId);
  const [sessions, setSessions] = useState<ChatSession[]>(boot.sessions);
  const [currentSessionId, setCurrentSessionId] = useState(boot.currentId);
  const [sessionDrawerOpen, setSessionDrawerOpen] = useState(false);
  const [omniHudExpanded, setOmniHudExpanded] = useState(false);

  useEffect(() => {
    currentSessionIdRef.current = currentSessionId;
  }, [currentSessionId]);

  useEffect(() => {
    void initVoiceCompanionDebugLog();
    void initVoiceChatTraceLog();
  }, []);

  /** 删除会话后若 current 已不存在，自动落到列表首条（含删到 0 条时新建的空白会话） */
  useEffect(() => {
    if (sessions.length === 0) return;
    if (!sessions.some((s) => s.id === currentSessionId)) {
      const pick = sessions[0];
      setCurrentSessionId(pick.id);
      currentSessionIdRef.current = pick.id;
    }
  }, [sessions, currentSessionId]);

  const [input, setInput] = useState("");
  /** Omni 多模态：待发附件（随 WebSocket attachments_metadata 发往 L3） */
  const [pendingFiles, setPendingFiles] = useState<File[]>([]);
  const pendingFilesRef = useRef<File[]>([]);
  const [attachmentHint, setAttachmentHint] = useState<string | null>(null);
  const [omniFileDragHighlight, setOmniFileDragHighlight] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [isRecording, setIsRecording] = useState(false);
  const [recordingStatus, setRecordingStatus] = useState<string>("");
  /** 录音过程中流式语音识别结果（Web Speech API 实时转写） */
  const [listeningText, setListeningText] = useState("");
  const [isTyping, setIsTyping] = useState(false);
  const chatAudioRef = useRef<HTMLAudioElement | null>(null);
  const bindChatAudio = useCallback((el: HTMLAudioElement | null) => {
    chatAudioRef.current = el;
    voicePlaybackController.setHostAudioElement(el);
    if (el) void voicePlaybackController.primeAutoplay();
  }, []);
  const typewriterCancelRef = useRef<(() => void) | null>(null);
  const { setState, ttsEnabled, ttsVoice } = useSpriteStore();
  const [desktopLang] = useDesktopUiLang();
  const desktopUi = useMemo(() => getDesktopOmniUi(desktopLang), [desktopLang]);
  const sensory = useSensoryWebSocket({ desktopSessionIdRef: currentSessionIdRef });
  const {
    handoffEvent,
    swarmEvent,
    streamChunkKind: wsStreamChunkKind,
    registerChunkHandler,
    registerAnswerHandler,
    registerStepHandler,
    registerMirrorInputHandler,
    registerBackgroundTaskHandler,
    sendInput,
    sendVoiceDiagnosticsAppend,
    sendToolUiResult,
    sendSessionClearControl,
    sendPrepareContextControl,
    sendRunAbort,
    memoryCompactSuggest,
    sendMemoryCompactControl,
    dismissMemoryCompactSuggest,
    zombieTasksPending,
    dismissZombieTasksPending,
    backgroundTaskPulse,
  } = sensory;

  /** 首次出现 HUD 信号（后台/Zombie/记忆）时自动展开动态岛；全部清除后可再次触发 */
  const hudHadSignalRef = useRef(false);
  useEffect(() => {
    const zombieActive = zombieTasksPending != null && zombieTasksPending.count > 0;
    const hasHudSignal =
      Boolean(backgroundTaskPulse) || zombieActive || Boolean(memoryCompactSuggest);
    if (hasHudSignal && !hudHadSignalRef.current) {
      setOmniHudExpanded(true);
      hudHadSignalRef.current = true;
    }
    if (!hasHudSignal) hudHadSignalRef.current = false;
  }, [backgroundTaskPulse, zombieTasksPending, memoryCompactSuggest]);

  const messages = useMemo(
    () => sessions.find((s) => s.id === currentSessionId)?.messages ?? [],
    [sessions, currentSessionId],
  );

  const updateSessionMessagesById = useCallback(
    (sessionId: string, updater: (m: StoredMessage[]) => StoredMessage[]) => {
      setSessions((prev) => {
        const idx = prev.findIndex((s) => s.id === sessionId);
        if (idx < 0) return prev;
        const cur = prev[idx];
        const nextMsgs = updater(cur.messages);
        let title = cur.title;
        if (title === "新对话" || !title.trim()) {
          const nt = titleFromMessages(nextMsgs);
          if (nt !== "新对话") title = nt;
        }
        const nextS: ChatSession = { ...cur, messages: nextMsgs, title, updatedAt: Date.now() };
        const rest = prev.filter((_, i) => i !== idx);
        return [nextS, ...rest].sort((a, b) => b.updatedAt - a.updatedAt);
      });
    },
    [],
  );

  const setMessages = useCallback(
    (updater: React.SetStateAction<StoredMessage[]>) => {
      const sid = currentSessionIdRef.current;
      updateSessionMessagesById(sid, (prev) =>
        typeof updater === "function" ? (updater as (p: StoredMessage[]) => StoredMessage[])(prev) : updater,
      );
    },
    [updateSessionMessagesById],
  );

  useEffect(() => {
    persistSessionsState(sessions, currentSessionId);
  }, [sessions, currentSessionId]);

  useEffect(() => {
    pendingFilesRef.current = pendingFiles;
  }, [pendingFiles]);

  const mergeOmniPendingFiles = useCallback((incoming: File[]) => {
    if (!incoming.length) return;
    const { next, hint } = mergePendingAttachmentFiles(pendingFilesRef.current, incoming);
    pendingFilesRef.current = next;
    setPendingFiles(next);
    setAttachmentHint(hint);
  }, []);

  const removeOmniPendingFile = useCallback((index: number) => {
    setPendingFiles((prev) => prev.filter((_, i) => i !== index));
    setAttachmentHint(null);
  }, []);

  const handleNewChat = useCallback(() => {
    registerChunkHandler(null);
    registerAnswerHandler(null);
    registerStepHandler(null);
    setIsLoading(false);
    setIsTyping(false);
    setInput("");
    setPendingFiles([]);
    setAttachmentHint(null);
    setRecordingStatus("");
    setSessionDrawerOpen(false);
    const ns = newEmptySession();
    setSessions((prev) => [ns, ...prev]);
    setCurrentSessionId(ns.id);
    currentSessionIdRef.current = ns.id;
  }, [registerChunkHandler, registerAnswerHandler, registerStepHandler]);

  const handleSelectSession = useCallback(
    (id: string) => {
      registerChunkHandler(null);
      registerAnswerHandler(null);
      registerStepHandler(null);
      setIsLoading(false);
      setIsTyping(false);
      setInput("");
      setSessionDrawerOpen(false);
      setCurrentSessionId(id);
      currentSessionIdRef.current = id;
    },
    [registerChunkHandler, registerAnswerHandler, registerStepHandler],
  );

  const handleDeleteSession = useCallback(
    (id: string) => {
      registerChunkHandler(null);
      registerAnswerHandler(null);
      registerStepHandler(null);
      setIsLoading(false);
      setIsTyping(false);
      setSessions((prev) => {
        const filtered = prev.filter((s) => s.id !== id);
        if (filtered.length === 0) return [newEmptySession()];
        return [...filtered].sort((a, b) => b.updatedAt - a.updatedAt);
      });
    },
    [registerChunkHandler, registerAnswerHandler, registerStepHandler],
  );

  /** L2 等无 Sensory 累积串时，按 chunk 元数据同步 Core 相位 */
  const [localStreamChunkKind, setLocalStreamChunkKind] = useState<StreamChunkKind | null>(null);
  const streamChunkKindEffective = wsStreamChunkKind ?? localStreamChunkKind;
  const jachinCore = useJachinCoreState(sensory, { isTyping, localStreamChunkKind });
  /** 安全指令协议：safe | warning(COMMAND) | danger(高风险待确认) */
  const [riskLevel, setRiskLevel] = useState<"safe" | "warning" | "danger">("safe");
  const [pendingHighRisk, setPendingHighRisk] = useState<{ text: string; strippedText: string } | null>(null);
  /** VAD 监听模式（双轨输入）：与 Rust 引擎 start_voice_capture / stop_voice_capture 联动 */
  const [isVadActive, setIsVadActive] = useState(false);
  /** L2 可用：打字与语音均直连 L2，不依赖 L3 Sensory */
  const [l2Available, setL2Available] = useState(true);
  const isRecordingRef = useRef(false);
  const isVadActiveRef = useRef(false);
  /** stop 后等待 Rust STT_AUDIO_READY，勿与 isRecordingRef 共用（UI 已结束但事件尚未到达） */
  const pttFinalizePendingRef = useRef(false);
  const pttAudioWaitTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  /**
   * Rust PTT 路径会“同步返回 WAV”且“同时发 STT_AUDIO_READY 事件”。
   * 在竞态下同一段音频可能被提交两次，这里做一次轻量指纹去重。
   */
  const lastVoiceSubmitRef = useRef<{ fp: string; at: number } | null>(null);
  const activeSttAbortRef = useRef<AbortController | null>(null);
  const voiceSttRequestSeqRef = useRef(0);
  isRecordingRef.current = isRecording;
  isVadActiveRef.current = isVadActive;
  /** 当前轮 L3 WS run_id，用于超时后仍接收 answer 时丢弃陈旧回复 */
  const l3ActiveRunIdRef = useRef<string>("");
  /** 用户停止或发起新轮时递增，使旧 chunk/answer 回调失效 */
  const chatTurnTokenRef = useRef(0);
  const activeChatTurnTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const l2StreamAbortRef = useRef<AbortController | null>(null);
  /** PTT 时 Web Audio 电平 → 声波条 */
  const [micLevel, setMicLevel] = useState(0);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const micRafRef = useRef(0);
  const [ttsPlaying, setTtsPlaying] = useState(false);
  /** 供 voice_chat.log 快照：避免 async 回调闭包读到陈旧 UI 态 */
  const voiceTraceUiRef = useRef<VoiceChatUiState>({});
  const [hudOrbState, setHudOrbState] = useState<AiState | null>(null);
  const hudOrbIdleResetTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  /** Lark 镜像回调：用 ref 读加载态，避免 effect 随 isLoading 反复卸载/重挂载导致抢答/丢 chunk */
  const isLoadingRef = useRef(false);
  const isTypingRef = useRef(false);
  /** 语音陪伴 / simulate：L3 流由 chat WS 接收，JVS TTS 在 chat 窗播放（陪伴 Orb 同窗） */
  const { companionMode, setCompanionMode, companionModeRef, voiceCompanionActiveRef, ensureCompanionSurfaceVisible } =
    useCompanionMode();
  /** HUD 自有 WS 回包兜底：避免临时窗有字但 chat 未接管 TTS。 */
  const hudTtsBridgeRunRef = useRef("");
  /** 最近一次打断（barge-in）标记：下一轮发给 L3，用于“好的，听你的”衔接语气。 */
  const justBargedInRef = useRef(false);
  /** 防抖：避免毫秒级重复 barge-in 把刚入队音频反复停掉，造成“始终不播报”的体感。 */
  const lastBargeInAtRef = useRef(0);
  /** 最近一次唤醒时间：用于告知 L3 当前可能处于唤醒首句场景。 */
  const lastWakeUpAtRef = useRef(0);
  /** 语音会话中的后台任务上下文：只作为 L3 证据，不做前端路由。 */
  const activeVoiceTasksRef = useRef<Map<string, VoiceTaskRef>>(new Map());
  const lastFocusVoiceTaskIdRef = useRef<string | null>(null);
  /** 大窗 PTT/VAD：走 JVS TTS，不绑 Orb。 */
  const chatJvsVoiceActiveRef = useRef(false);
  const lastJvsWarmAtRef = useRef(0);
  const startCompanionJvsIfNeeded = useCallback(() => {
    const warmIfDue = () => {
      const now = Date.now();
      if (now - lastJvsWarmAtRef.current < 60_000) return;
      lastJvsWarmAtRef.current = now;
      void warmJvsAudioModels({ stt: true, tts: true, sv: false }).catch((e) => {
        voiceCompanionDebug("chat.jvs_warm_warn", { error: String(e) });
      });
    };
    void getJvsHealth()
      .then(warmIfDue)
      .catch(() => startJvsProcess().then(warmIfDue).catch((e) => {
        console.warn("[VoiceCompanion] JVS start failed:", e);
      }));
  }, []);
  const prewarmPttSttStream = useCallback((reason: string) => {
    void invoke<string>("prewarm_ptt_stt_stream")
      .then((sessionId) => {
        voiceChatTrace("stt.stream_prewarm_ok", { reason, sessionId });
      })
      .catch((e) => {
        voiceCompanionDebug("chat.stt_stream_prewarm_warn", { reason, error: String(e) });
      });
  }, []);
  useEffect(() => {
    prewarmPttSttStream("chat_mount");
  }, [prewarmPttSttStream]);
  isLoadingRef.current = isLoading;
  isTypingRef.current = isTyping;

  const stopMicAnalyser = () => {
    if (micRafRef.current) {
      cancelAnimationFrame(micRafRef.current);
      micRafRef.current = 0;
    }
    if (audioCtxRef.current) {
      void audioCtxRef.current.close();
      audioCtxRef.current = null;
    }
    setMicLevel(0);
  };

  // L2 健康检查：打字和语音都走 L2，只需 L2 可用即可
  useEffect(() => {
    let mounted = true;
    const check = async () => {
      try {
        await checkHealth();
        if (mounted) setL2Available(true);
      } catch {
        if (mounted) setL2Available(false);
      }
    };
    check();
    const interval = setInterval(check, 5000);
    return () => {
      mounted = false;
      clearInterval(interval);
    };
  }, []);

  // 不在此用 window blur 自动隐藏：dev 环境下焦点常在 PowerShell/IDE，WebView 收不到 focus，
  // 延迟隐藏仍会在数百毫秒后执行 → Omni「闪退」。收起请用 Esc、托盘左键或窗口关闭。

  /** 断电遗留横幅出现时：陪伴圆/最小化时走哨兵 Toast（与后台任务完成一致） */
  useEffect(() => {
    if (!zombieTasksPending || zombieTasksPending.count < 1) return;
    void maybeNotifyJachinAssistantDone(
      `断电遗留 ${zombieTasksPending.count} 条后台任务未闭环，可让助手调用 core:check_interrupted_tasks`,
      "answer",
    );
  }, [zombieTasksPending]);

  /** 与 ChatPanel 一致：后台任务完成/失败时写入会话并可选哨兵通知（主 Omni 此前未注册 handler，收不到 l3_event_bus 推送） */
  useEffect(() => {
    registerBackgroundTaskHandler((ev) => {
      if (ev.event === "zombie_tasks_pending") {
        return;
      }
      const taskId = ev.task_id;
      if (!taskId) return;
      if (ev.event === "queued" || ev.event === "started" || ev.event === "pulse") {
        const title = (ev.intent_preview || "").trim();
        activeVoiceTasksRef.current.set(taskId, { id: taskId, title: title || undefined });
      }
      if (ev.event === "completed" || ev.event === "failed" || ev.event === "cancelled") {
        activeVoiceTasksRef.current.delete(taskId);
        if (lastFocusVoiceTaskIdRef.current === taskId) {
          lastFocusVoiceTaskIdRef.current = null;
        }
      }
      if (ev.event !== "completed" && ev.event !== "failed" && ev.event !== "cancelled") {
        return;
      }
      let text = "";
      if (ev.event === "completed") {
        const preview = (ev.result_preview || "").trim();
        text =
          `### 后台任务已完成\n\n` +
          `- **任务 ID：** \`${taskId}\`\n` +
          (preview ? `\n**结果摘要：**\n\n${preview}\n` : "\n") +
          `\n如需完整输出，可在对话中说明「查询该任务结果」或请助手调用 \`core:check_background_task\`（传入该 task_id）。`;
        void maybeNotifyJachinAssistantDone(
          `后台任务完成 ${taskId}：${summarizeForSentryNotify(preview || "已完成")}`,
          "answer",
        );
      } else if (ev.event === "failed") {
        text =
          `### 后台任务失败\n\n- **任务 ID：** \`${taskId}\`` +
          (ev.message ? `\n\n**原因：** ${ev.message}` : "");
        void maybeNotifyJachinAssistantDone(`后台任务失败：${ev.message || taskId}`, "error");
      } else {
        text = `### 后台任务已取消\n\n- **任务 ID：** \`${taskId}\``;
      }
      const msg: StoredMessage = {
        role: "assistant",
        content: text,
        reasoning: "",
        timestamp: Date.now(),
        source: "L3",
      };
      setMessages((prev) => [...prev, msg]);
    });
    return () => registerBackgroundTaskHandler(null);
  }, [registerBackgroundTaskHandler, setMessages]);

  useEffect(() => {
    let unlisten: (() => void) | undefined;
    void listen<{ state?: string }>("hud-orb-state", (ev) => {
      const s = (ev.payload?.state ?? "").toLowerCase();
      if (s !== "idle" && s !== "listening" && s !== "thinking" && s !== "speaking") return;
      const next = s as AiState;
      setHudOrbState(next);
      if (hudOrbIdleResetTimerRef.current) {
        clearTimeout(hudOrbIdleResetTimerRef.current);
        hudOrbIdleResetTimerRef.current = null;
      }
      if (next === "idle") {
        hudOrbIdleResetTimerRef.current = setTimeout(() => {
          setHudOrbState(null);
          hudOrbIdleResetTimerRef.current = null;
        }, 2200);
      }
    })
      .then((fn) => {
        unlisten = fn;
      })
      .catch(() => {});
    return () => {
      unlisten?.();
      if (hudOrbIdleResetTimerRef.current) {
        clearTimeout(hudOrbIdleResetTimerRef.current);
        hudOrbIdleResetTimerRef.current = null;
      }
    };
  }, []);

  const requestHideChat = useCallback(async () => {
    void desktopDiagLog("react_hide_chat_request", { phase: "before_invoke" });
    try {
      const r = await invoke<HideChatWindowResult>("hide_chat_window");
      if (!r?.companion) {
        throw new Error("hide_chat_window returned companion=false");
      }
      setCompanionMode(true);
      scheduleCompanionLayoutSyncWithRetry();
      void desktopDiagLog("react_hide_chat_ok", {
        companion: r.companion,
        fullyHidden: r.fullyHidden,
      });
    } catch (err) {
      console.error("[Omni] hide_chat_window failed:", err);
      void desktopDiagLog("react_hide_chat_err", { err: String(err) });
      try {
        const rustCompanion = await invoke<boolean>("is_chat_companion_mode");
        if (rustCompanion) {
          setCompanionMode(true);
          await invoke("companion_restore_surface");
          await getCurrentWindow().show().catch(() => {});
          scheduleCompanionLayoutSyncWithRetry();
          return;
        }
      } catch {
        // fall through
      }
      setCompanionMode(false);
    }
  }, [setCompanionMode]);

  const requestExpandFromSpark = useCallback(async () => {
    void desktopDiagLog("react_expand_from_spark", { phase: "before_invoke" });
    try {
      await invoke("show_chat_window");
      setCompanionMode(false);
      void desktopDiagLog("react_expand_from_spark", { phase: "ok" });
    } catch (err) {
      console.error("[Omni] show_chat_window failed:", err);
      void desktopDiagLog("react_expand_from_spark", { phase: "err", err: String(err) });
    }
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      e.stopPropagation();
      void requestHideChat();
    };
    // 捕获阶段：避免输入框等子组件消费 Esc 后事件到不了 window
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [requestHideChat]);

  // 唤醒词：陪伴链路 + Orb 状态（Earcon 由 Rust 播放）
  useEffect(() => {
    let unlisten: (() => void) | undefined;
    listen<{ wake_word?: string; source?: string }>("WAKE_UP", (ev) => {
      voiceCompanionActiveRef.current = true;
      lastWakeUpAtRef.current = Date.now();
      voiceCompanionDebug("chat.wake_up", {
        wake_word: ev.payload?.wake_word ?? "",
        source: ev.payload?.source ?? "",
      });
      voiceSessionStore.setState("listening");
      void armCompanionVoiceSession();
      void ensureCompanionSurfaceVisible();
    })
      .then((fn) => {
        unlisten = fn;
      })
      .catch(() => {});
    return () => {
      unlisten?.();
    };
  }, [ensureCompanionSurfaceVisible]);

  // 桌面启动后自动拉起唤醒监听（与 Rust auto_start 双保险；需 ambient 构建）
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const running = await invoke<boolean>("stt_wake_listener_running");
        if (cancelled || running) return;
        const settings = await invoke<{ sprite_voice_mode?: string | null; wake_word?: string | null }>(
          "get_user_settings",
        );
        const mode = settings?.sprite_voice_mode?.trim() || "push_to_talk";
        const autoEnv =
          typeof import.meta !== "undefined" &&
          (import.meta as { env?: Record<string, string> }).env?.VITE_JACHIN_AUTO_WAKE === "1";
        if (mode !== "wake_up" && !autoEnv) return;
        await invoke("stt_start_wake_listener", {
          wake_word: settings?.wake_word?.trim() || undefined,
        });
        voiceCompanionDebug("chat.wake_auto_start", { mode, wake_word: settings?.wake_word ?? "" });
      } catch {
        /* ambient 未编译或 VAD 缺失时忽略 */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // 语音模拟脚本注入：将脚本发送的 user/assistant 文本写入当前会话历史（用于联调 HUD + Companion）。
  useEffect(() => {
    let unlisten: (() => void) | undefined;
    listen<{ role?: string; content?: string }>("voice-sim-message", (ev) => {
      const role = (ev.payload?.role ?? "").toLowerCase();
      const content = typeof ev.payload?.content === "string" ? ev.payload.content.trim() : "";
      if (!content) return;
      if (role !== "user" && role !== "assistant") return;
      const msg: StoredMessage = {
        role: role as "user" | "assistant",
        content,
        reasoning: role === "assistant" ? "" : undefined,
        timestamp: Date.now(),
        source: "L3",
      };
      setMessages((prev) => [...prev, msg]);
    })
      .then((fn) => {
        unlisten = fn;
      })
      .catch(() => {});
    return () => {
      unlisten?.();
    };
  }, [setMessages]);

  useEffect(() => {
    let unlisten: (() => void) | undefined;
    void listen<{ active?: boolean }>("hud-voice-session", (ev) => {
      voiceCompanionActiveRef.current = Boolean(ev.payload?.active);
      voiceCompanionDebug("chat.hud_voice_session", { active: voiceCompanionActiveRef.current });
    })
      .then((fn) => {
        unlisten = fn;
      })
      .catch(() => {});
    return () => {
      unlisten?.();
    };
  }, []);

  useEffect(() => {
    return voiceSessionStore.subscribe((st) => {
      if (!voiceCompanionActiveRef.current) return;
      if (st === "error") return;
      void emit("hud-orb-state", { state: st }).catch(() => {});
    });
  }, []);

  /**
   * 生成式 UI：经 L3 WebSocket `tool_ui_result` 执行 Native 工具，answer 帧写回同一条气泡并标记 resolved。
   */
  const handleToolUiResult = useCallback(
    async (payload: ToolUiSubmitPayload): Promise<void> => {
      const toolSid = currentSessionIdRef.current;
      registerStepHandler(null);
      setIsTyping(true);
      await new Promise<void>((resolve) => {
        let settled = false;
        const finish = () => {
          if (settled) return;
          settled = true;
          registerAnswerHandler(null);
          setIsTyping(false);
          setIsLoading(false);
          resolve();
        };
        const timer = window.setTimeout(() => {
          registerAnswerHandler(null);
          updateSessionMessagesById(toolSid, (prev) => {
            const idx = findUnresolvedToolCallMessageIndex(prev, payload);
            if (idx < 0) return prev;
            const next = [...prev];
            const cur = next[idx];
            next[idx] = {
              ...cur,
              tool_call: { ...cur.tool_call!, resolved: true },
              content: "提交超时：未收到 L3 回复。请确认 ws://127.0.0.1:18981 已连接且已重启 L3。",
              source: "L3",
            };
            return next;
          });
          finish();
        }, CHAT_RESPONSE_TIMEOUT_MS);

        registerAnswerHandler((answerContent) => {
          window.clearTimeout(timer);
          registerAnswerHandler(null);
          updateSessionMessagesById(toolSid, (prev) => {
            const idx = findUnresolvedToolCallMessageIndex(prev, payload);
            if (idx < 0) {
              return prev;
            }
            const next = [...prev];
            const cur = next[idx];
            const n = normalizeAssistantOutput(answerContent || "");
            next[idx] = {
              ...cur,
              tool_call: { ...cur.tool_call!, resolved: true },
              content: n.content,
              reasoning: [cur.reasoning ?? "", n.reasoning].filter(Boolean).join("\n\n").trim(),
              source: "L3",
            };
            return next;
          });
          void maybeNotifyJachinAssistantDone(summarizeForSentryNotify(answerContent || ""), "answer");
          finish();
        });

        const sent = sendToolUiResult({
          toolName: payload.toolName,
          toolCallId: payload.toolCallId,
          result: payload.result,
        });
        if (!sent) {
          window.clearTimeout(timer);
          registerAnswerHandler(null);
          updateSessionMessagesById(toolSid, (prev) => {
            const idx = findUnresolvedToolCallMessageIndex(prev, payload);
            if (idx < 0) return prev;
            const next = [...prev];
            const cur = next[idx];
            next[idx] = {
              ...cur,
              tool_call: { ...cur.tool_call!, resolved: true },
              content: "无法连接 L3 WebSocket，参数未送达。请启动 L3 后再试。",
              source: "L3",
            };
            return next;
          });
          finish();
        }
      });
    },
    [registerAnswerHandler, registerStepHandler, sendToolUiResult, updateSessionMessagesById],
  );

  // 滚动由 HolographicChat 内部的 messagesEndRef 处理，此处不重复

  // Lark 镜像：Lark 用户发消息时，终端同步显示并接收后续回复
  useEffect(() => {
    const handler = (content: string) => {
      if (!content.trim() || isLoadingRef.current || isTypingRef.current) return;
      const mirrorSid = currentSessionIdRef.current;
      const displayContent = `[Lark] ${content.trim()}`;
      const userMsg: StoredMessage = { role: "user", content: displayContent, timestamp: Date.now() };
      updateSessionMessagesById(mirrorSid, (m) => [...m, userMsg]);
      const assistantMsg: StoredMessage = { role: "assistant", content: "", reasoning: "", timestamp: Date.now() };
      updateSessionMessagesById(mirrorSid, (m) => [...m, assistantMsg]);
      setIsLoading(true);
      setIsTyping(true);
      setLocalStreamChunkKind(null);
      let mirrorStreamMerge = "";
      const mirrorRunIdRef = { current: "" };
      const chunkHandler = (chunk: string, runId?: string, meta?: SensoryChunkMeta) => {
        if (runId) mirrorRunIdRef.current = runId;
        setLocalStreamChunkKind(meta?.isReasoning ? "reasoning" : "content");
        if (meta?.isReasoning) {
          updateSessionMessagesById(mirrorSid, (prev) => {
            const u = [...prev];
            const last = u[u.length - 1];
            if (last?.role !== "assistant") return prev;
            const merged = mergeAssistantFlatAndSplitFinalAnswer(last, chunk, meta);
            u[u.length - 1] = { ...last, content: merged.content, reasoning: merged.reasoning };
            return u;
          });
          return;
        }
        const { next, delta } = mergeStreamChunk(mirrorStreamMerge, chunk);
        mirrorStreamMerge = next;
        if (!delta) return;
        updateSessionMessagesById(mirrorSid, (prev) => {
          const u = [...prev];
          const last = u[u.length - 1];
          if (last?.role !== "assistant") return prev;
          const merged = mergeAssistantFlatAndSplitFinalAnswer(last, delta, {
            ...meta,
            isReasoning: false,
          });
          u[u.length - 1] = { ...last, content: merged.content, reasoning: merged.reasoning };
          return u;
        });
      };
      const stepHandler = (_step: string, stepContent: string, runId?: string) => {
        if (runId) mirrorRunIdRef.current = runId;
        updateSessionMessagesById(mirrorSid, (prev) => {
          const u = [...prev];
          const last = u[u.length - 1];
          if (last?.role === "assistant") {
            const r = (last.reasoning ?? "") + stepContent;
            u[u.length - 1] = { ...last, reasoning: r };
          }
          return u;
        });
      };
      const answerHandler = (answerContent: string, meta?: SensoryAnswerMeta) => {
        const rid = meta?.runId ?? "";
        if (rid && mirrorRunIdRef.current && !l3RunIdsSameTurn(rid, mirrorRunIdRef.current)) return;
        const hadStream = meta?.hadStreamChunks ?? false;
        const useServerFinal = hadStream && !!(answerContent || "").trim();
        updateSessionMessagesById(mirrorSid, (prev) => {
          const u = [...prev];
          const last = u[u.length - 1];
          if (last?.role === "assistant") {
            if (hadStream && !useServerFinal) {
              const combined = [last.reasoning ?? "", last.content ?? ""].filter(Boolean).join("\n\n");
              const n = normalizeAssistantOutput(combined);
              u[u.length - 1] = { ...last, content: n.content, reasoning: n.reasoning, source: "L3" };
            } else {
              let newContent = useServerFinal
                ? answerContent
                : !hadStream
                  ? answerContent || last.content
                  : last.content;
              let newReasoning = last.reasoning ?? "";
              const n = normalizeAssistantOutput(newContent ?? "");
              newContent = n.content;
              newReasoning = [newReasoning, n.reasoning].filter(Boolean).join("\n\n").trim();
              u[u.length - 1] = {
                ...last,
                content: newContent,
                reasoning: newReasoning,
                source: "L3",
              };
            }
          }
          return u;
        });
        setIsLoading(false);
        setIsTyping(false);
        setLocalStreamChunkKind(null);
        registerChunkHandler(null);
        registerAnswerHandler(null);
        registerStepHandler(null);
        const summaryText =
          useServerFinal || !hadStream
            ? (answerContent || "").trim()
            : "流式回复已就绪";
        const sv: SentryNotifyVariant =
          meta?.terminalOutcome === "rejected"
            ? "rejected"
            : meta?.terminalOutcome === "error"
              ? "error"
              : "answer";
        void maybeNotifyJachinAssistantDone(summarizeForSentryNotify(summaryText), sv);
      };
      registerChunkHandler(chunkHandler);
      registerStepHandler(stepHandler);
      registerAnswerHandler(answerHandler);
    };
    registerMirrorInputHandler(handler);
    return () => registerMirrorInputHandler(null);
  }, [registerMirrorInputHandler, registerChunkHandler, registerAnswerHandler, registerStepHandler, updateSessionMessagesById]);


  /** 实际发送消息：优先 L3 Sensory，未连接时直连 L2 文本 API（与语音同源） */
  const doActualSend = async (
    content: string,
    attachmentFiles: File[] = [],
    opts?: {
      extraImplicitSignals?: Record<string, unknown>;
      displayContent?: string;
      assistantCueText?: string;
      assistantCueReason?: string;
    },
  ) => {
    if (content.trim() === "/clear") {
      /* reasoningPulseTimer 仅存在于进行中的 doActualSend；此处仅清处理器 */
      registerChunkHandler(null);
      registerAnswerHandler(null);
      registerStepHandler(null);
      const clearSid = currentSessionIdRef.current;
      const systemLine: StoredMessage = {
        role: "system",
        content: "🧹 统帅，当前会话上下文已物理清空，大模型已进入失忆状态。",
        timestamp: Date.now(),
      };
      updateSessionMessagesById(clearSid, () => [systemLine]);
      setInput("");
      setPendingFiles([]);
      setAttachmentHint(null);
      setIsLoading(false);
      setIsTyping(false);
      setRiskLevel("safe");
      setState("idle");
      setLocalStreamChunkKind(null);
      l3ActiveRunIdRef.current = "";
      sendSessionClearControl();
      return;
    }

    const filesSnapshot = [...attachmentFiles];
    /** 大附件 Base64 期间须先进入加载态，否则主线程长时间无反馈像「卡死」 */
    const hasAttachments = filesSnapshot.length > 0;
    if (hasAttachments) {
      setIsLoading(true);
      setIsTyping(true);
      setState("thinking");
    }
    const attBuilt = await buildAttachmentsMetadataPayload(filesSnapshot);
    if (!attBuilt.ok) {
      if (hasAttachments) {
        setIsLoading(false);
        setIsTyping(false);
        setState("idle");
      }
      setAttachmentHint(attBuilt.error);
      return;
    }
    if (filesSnapshot.length > 0 && !sensory.connected) {
      if (hasAttachments) {
        setIsLoading(false);
        setIsTyping(false);
        setState("idle");
      }
      setAttachmentHint("发送附件需已连接 Layer 3（Sensory WebSocket，ws://localhost:18981）");
      return;
    }

    const turnSessionId = currentSessionIdRef.current;
    const namesLine =
      filesSnapshot.length > 0
        ? `📎 ${filesSnapshot.map((f) => f.name).join(", ")}`
        : "";
    const displayContent = opts?.displayContent?.trim() || content.trim();
    const userBubbleText = [displayContent, namesLine].filter(Boolean).join("\n\n");
    const userMessage: StoredMessage = { role: "user", content: userBubbleText, timestamp: Date.now() };
    const assistantMessage: StoredMessage = { role: "assistant", content: "", reasoning: "", timestamp: Date.now() };
    updateSessionMessagesById(turnSessionId, (m) => [...m, userMessage, assistantMessage]);
    setInput("");
    setPendingFiles([]);
    setAttachmentHint(null);
    setIsLoading(true);
    setRiskLevel("safe");
    setState("thinking");
    setIsTyping(true); /* 无附件时此处首次进入 typing；有附件时与编码阶段一致保持为 true */
    l3ActiveRunIdRef.current = "";
    const chatVoiceSpeakSentences = chatJvsVoiceActiveRef.current
      ? VOICE_PROFILES.chat_ptt.maxSpeakSentences
      : resolveChatSpeakSentences(ttsEnabled);
    const forceCompanionVoice = voiceCompanionActiveRef.current || companionModeRef.current;
    const companionJvsVoice = resolveCompanionJvsVoice(ttsVoice);
    if (forceCompanionVoice) {
      // 陪伴窗内优先保持 companion 语音链路，避免 HUD 会话事件抖动导致 ref 短暂 false 而丢播报。
      voiceCompanionActiveRef.current = true;
      voiceOrchestrator.startSession(`companion-${turnSessionId}-${Date.now()}`, {
        ttsVoice: companionJvsVoice,
      });
      startCompanionJvsIfNeeded();
      voiceOrchestrator.onL3Thinking();
      voiceCompanionDebug("chat.companion_send_start", {
        turnSessionId,
        text: truncVoiceLog(content, 120),
        sensoryConnected: sensory.connected,
        ttsVoiceRaw: ttsVoice ?? "",
        ttsVoiceResolved: companionJvsVoice,
      });
      void emitCompanionL3ToHud({ kind: "thinking" });
    } else if (chatJvsVoiceActiveRef.current && chatVoiceSpeakSentences > 0) {
      voiceOrchestrator.startSession(`chat-voice-${turnSessionId}-${Date.now()}`, {
        maxSpeakSentences: chatVoiceSpeakSentences,
        companionUi: false,
        ttsVoice: DEFAULT_KOKORO_TTS_VOICE,
      });
      startCompanionJvsIfNeeded();
      voiceChatTraceIfActive("tts.session_armed", {
        maxSpeakSentences: chatVoiceSpeakSentences,
        ttsEnabled,
        ttsVoiceResolved: DEFAULT_KOKORO_TTS_VOICE,
      });
    }
    chatTurnTokenRef.current += 1;
    const myTurnToken = chatTurnTokenRef.current;
    l2StreamAbortRef.current?.abort();
    const l2Abort = new AbortController();
    l2StreamAbortRef.current = l2Abort;
    setLocalStreamChunkKind(null);

    const audioEl = chatAudioRef.current;
    const useJvsCompanionVoice = forceCompanionVoice;
    const useJvsChatVoice = chatJvsVoiceActiveRef.current && chatVoiceSpeakSentences > 0;
    const useJvsOrchestrator = useJvsCompanionVoice || useJvsChatVoice;
    const ttsQueue =
      !useJvsOrchestrator && ttsEnabled && audioEl ? createAudioQueue(audioEl, () => setState("idle")) : null;
    let accumulatedForTts = "";

    const assistantCueText = opts?.assistantCueText?.trim() || "";
    if (assistantCueText) {
      if (useJvsOrchestrator) {
        voiceCompanionDebug("chat.assistant_cue_speak", {
          cue: truncVoiceLog(assistantCueText, 80),
          reason: opts?.assistantCueReason ?? "",
          companion: useJvsCompanionVoice,
          chatVoice: useJvsChatVoice,
        });
        void voiceOrchestrator.speakCue(assistantCueText, opts?.assistantCueReason ?? "voice_latency_masking");
      } else {
        voiceCompanionDebug("chat.assistant_cue_skip", {
          cue: truncVoiceLog(assistantCueText, 80),
          reason: "voice_orchestrator_inactive",
        });
      }
    }

    const shouldSpeakFinalAnswer = (text: string, meta?: SensoryAnswerMeta): boolean => {
      if (!text.trim()) return false;
      if (meta?.terminalOutcome === "error" || meta?.terminalOutcome === "rejected") return false;
      if (!meta?.hadStreamChunks) return true;
      const compactSpoken = accumulatedForTts.replace(/[\s\u3000，,。.!！?？、；;：:]+/g, "");
      return compactSpoken.length > 0 && compactSpoken.length <= 4;
    };
    const enqueueSentence = (sentence: string) => {
      if (!sentence.trim() || !ttsQueue) return;
      synthesizeSpeech(sentence.trim(), ttsVoice)
        .then((blob) => { ttsQueue.enqueue(blob); setState("speaking"); })
        .catch(() => {});
    };

    let timeoutCleared = false;
    /**
     * L2 等直连流式：chunk 可能是全量累加字符串，需 merge。
     * L3 Sensory：hook 内已 merge，此处仅收到 delta，merge 后与 prev 拼接仍正确。
     */
    let streamMergeAcc = "";
    let companionVisibleStreamAcc = "";
    let lastCompanionTtsDelta = "";
    let lastCompanionHudDelta = "";
    let reasoningPulseTimer: ReturnType<typeof setInterval> | null = null;
    const l3StartedAt = Date.now();
    let firstContentChunkSeen = false;
    let lastSensoryActivityAt = Date.now();
    const clearReasoningPulse = () => {
      if (reasoningPulseTimer != null) {
        clearInterval(reasoningPulseTimer);
        reasoningPulseTimer = null;
      }
    };

    const cleanup = (
      finalContent: string,
      source?: "L3" | "L2",
      opts?: {
        skipContentUpdate?: boolean;
        ttsUseFinalOnly?: boolean;
        sentryVariant?: SentryNotifyVariant;
      },
    ) => {
      const sanitizedFinal = stripDefaultSadEmojiSuffix(finalContent || "");
      if (myTurnToken !== chatTurnTokenRef.current) return;
      if (timeoutCleared) return;
      timeoutCleared = true;
      voiceChatTraceIfActive("l3.cleanup", {
        source: source ?? "unknown",
        finalPreview: truncChatTrace(finalContent, 400),
        finalLen: finalContent.length,
        turnToken: myTurnToken,
        runId: l3ActiveRunIdRef.current,
        skipContentUpdate: opts?.skipContentUpdate,
        ttsUseFinalOnly: opts?.ttsUseFinalOnly,
        sentryVariant: opts?.sentryVariant,
        ui: voiceTraceUiRef.current,
      });
      if (getActiveVoiceChatTraceId()) {
        const outcome = opts?.sentryVariant === "error" || opts?.sentryVariant === "rejected" ? "l3_error" : "ok";
        if (useJvsOrchestrator) {
          voiceChatTraceIfActive("turn.defer_end_for_tts", {
            outcome,
            source,
            finalLen: finalContent.length,
          });
        } else {
          endVoiceChatTrace(outcome, { source, finalLen: finalContent.length });
        }
      }
      void maybeNotifyJachinAssistantDone(sanitizedFinal || finalContent, opts?.sentryVariant ?? "answer");
      clearReasoningPulse();
      registerChunkHandler(null);
      clearTimeout(timeoutId);
      activeChatTurnTimeoutRef.current = null;
      updateSessionMessagesById(turnSessionId, (prev) => {
        const updated = [...prev];
        const last = updated[updated.length - 1];
        if (last?.role === "assistant") {
          if (opts?.skipContentUpdate) {
            const combined = [last.reasoning ?? "", last.content ?? ""].filter(Boolean).join("\n\n");
            const n = normalizeAssistantOutput(
              combined.trim() ? combined : (sanitizedFinal || finalContent || last.content || ""),
            );
            updated[updated.length - 1] = {
              ...last,
              content: stripDefaultSadEmojiSuffix(n.content),
              reasoning: n.reasoning,
              source,
            };
          } else {
            let newContent = sanitizedFinal || finalContent || last.content;
            let newReasoning = last.reasoning ?? "";
            const n = normalizeAssistantOutput(newContent ?? "");
            newContent = stripDefaultSadEmojiSuffix(n.content);
            newReasoning = [newReasoning, n.reasoning].filter(Boolean).join("\n\n").trim();
            updated[updated.length - 1] = { ...last, content: newContent, reasoning: newReasoning, source };
          }
        }
        return updated;
      });
      if (ttsQueue && (sanitizedFinal || finalContent)) {
        const ttsSource = opts?.ttsUseFinalOnly
          ? (sanitizedFinal || finalContent)
          : accumulatedForTts + (sanitizedFinal || finalContent);
        const { complete, remainder } = extractCompleteSentences(ttsSource);
        complete.forEach(enqueueSentence);
        if (remainder.trim()) enqueueSentence(remainder);
        ttsQueue.ensureIdle();
      }
      setIsLoading(false);
      setIsTyping(false);
      setLocalStreamChunkKind(null);
      setRiskLevel("safe");
      chatJvsVoiceActiveRef.current = false;
      if (!ttsQueue) setTimeout(() => setState("idle"), 2000);
      registerAnswerHandler(null);
      registerStepHandler(null);
    };

    const chunkHandler = (chunk: string, runId?: string, meta?: SensoryChunkMeta) => {
      if (myTurnToken !== chatTurnTokenRef.current) return;
      lastSensoryActivityAt = Date.now();
      if (runId) l3ActiveRunIdRef.current = runId;
      setLocalStreamChunkKind(meta?.isReasoning ? "reasoning" : "content");
      if (meta?.isReasoning) {
        voiceChatTraceIfActive("l3.chunk", {
          runId: runId ?? "",
          delta: truncChatTrace(chunk, 200),
          isReasoning: true,
          chunkKind: "reasoning",
        });
        updateSessionMessagesById(turnSessionId, (prev) => {
          const updated = [...prev];
          const last = updated[updated.length - 1];
          if (last?.role !== "assistant") return prev;
          const merged = mergeAssistantFlatAndSplitFinalAnswer(last, chunk, meta);
          updated[updated.length - 1] = { ...last, content: merged.content, reasoning: merged.reasoning };
          return updated;
        });
        return;
      }
      const { next, delta } = mergeStreamChunk(streamMergeAcc, chunk);
      streamMergeAcc = next;
      if (!delta) return;
      const companionVisibleAcc = stripAssistantUiProtocol(streamMergeAcc);
      const companionDelta = companionVisibleAcc.startsWith(companionVisibleStreamAcc)
        ? companionVisibleAcc.slice(companionVisibleStreamAcc.length)
        : companionVisibleAcc;
      companionVisibleStreamAcc = companionVisibleAcc;
      const firstChunkLatencyMs = firstContentChunkSeen ? undefined : Date.now() - l3StartedAt;
      firstContentChunkSeen = true;
      voiceChatTraceIfActive("l3.chunk", {
        runId: runId ?? "",
        delta: truncChatTrace(delta, 200),
        isReasoning: false,
        streamAccLen: streamMergeAcc.length,
        chunkKind: "content",
        firstChunkLatencyMs,
        latencyMs: Date.now() - l3StartedAt,
      });
      accumulatedForTts += delta;
      if (useJvsCompanionVoice) {
        if (companionDelta && companionDelta !== lastCompanionTtsDelta) {
          lastCompanionTtsDelta = companionDelta;
          void voiceOrchestrator.onL3Chunk(companionDelta);
        }
        if (companionDelta && companionDelta !== lastCompanionHudDelta) {
          lastCompanionHudDelta = companionDelta;
          voiceCompanionDebug("chat.companion_chunk", {
            delta: truncVoiceLog(companionDelta, 80),
            runId: runId ?? "",
            isReasoning: meta?.isReasoning ?? false,
          });
          void emitCompanionL3ToHud({
            kind: "chunk",
            delta: companionDelta,
            runId: runId ?? undefined,
            chunkMeta: meta,
          });
        }
      } else if (useJvsChatVoice) {
        if (companionDelta) void voiceOrchestrator.onL3Chunk(companionDelta);
      } else {
        voiceCompanionDebug("chat.l3_chunk_no_companion", {
          delta: truncVoiceLog(delta, 60),
          hint: "voiceCompanionActive=false，不会 TTS",
        });
      }
      updateSessionMessagesById(turnSessionId, (prev) => {
        const updated = [...prev];
        const last = updated[updated.length - 1];
        if (last?.role === "assistant") {
          const merged = mergeAssistantFlatAndSplitFinalAnswer(last, delta, {
            ...meta,
            isReasoning: false,
          });
          updated[updated.length - 1] = { ...last, content: merged.content, reasoning: merged.reasoning };
        }
        return updated;
      });
    };

    const stepHandler = (stepType: string, content: string, runId?: string) => {
      if (myTurnToken !== chatTurnTokenRef.current) return;
      lastSensoryActivityAt = Date.now();
      if (runId) l3ActiveRunIdRef.current = runId;
      voiceChatTraceIfActive("l3.step", {
        stepType,
        runId: runId ?? "",
        content: truncChatTrace(content, 240),
      });
      updateSessionMessagesById(turnSessionId, (prev) => {
        const updated = [...prev];
        const last = updated[updated.length - 1];
        if (last?.role === "assistant") {
          const reasoning = (last.reasoning ?? "") + content;
          updated[updated.length - 1] = { ...last, reasoning };
        }
        return updated;
      });
    };

    /** Waiting hint: show once only when the assistant bubble is still completely empty. */
    const REASONING_PULSE_MS = 12000;
    const pulseHints = [
      "调度管线等待上游 token（chunk 流式）…",
      "Sensory WebSocket 已连接；若长时间无输出多为模型或工具阻塞。",
      "仍在合并流式片段，L3 run 进行中。",
      "可检查网络、API Key 或缩小单次请求范围。",
    ];
    let pulseIdx = 0;
    let pulseShown = false;
    reasoningPulseTimer = window.setInterval(() => {
      if (myTurnToken !== chatTurnTokenRef.current) return;
      if (Date.now() - lastSensoryActivityAt < 900) return;
      updateSessionMessagesById(turnSessionId, (prev) => {
        if (pulseShown) return prev;
        const updated = [...prev];
        const last = updated[updated.length - 1];
        if (last?.role !== "assistant") return prev;
        if ((last.content ?? "").trim() || (last.reasoning ?? "").trim()) return prev;
        const ts = new Date().toLocaleTimeString();
        const line = `\n[${ts}] [jachin:heartbeat] ${pulseHints[pulseIdx % pulseHints.length]}\n`;
        pulseIdx += 1;
        pulseShown = true;
        updated[updated.length - 1] = { ...last, reasoning: (last.reasoning ?? "") + line };
        return updated;
      });
    }, REASONING_PULSE_MS);

    const timeoutId = setTimeout(() => {
      if (myTurnToken !== chatTurnTokenRef.current) return;
      voiceChatTraceIfActive("l3.timeout", {
        timeoutSec: CHAT_RESPONSE_TIMEOUT_SEC,
        turnToken: myTurnToken,
        runId: l3ActiveRunIdRef.current,
      });
      endVoiceChatTrace("timeout", { runId: l3ActiveRunIdRef.current });
      void maybeNotifyJachinAssistantDone(
        `响应超时（${CHAT_RESPONSE_TIMEOUT_SEC} 秒），请检查 Layer 3 或 Layer 2 状态。`,
        "error",
      );
      clearReasoningPulse();
      registerChunkHandler(null);
      registerStepHandler(null);
      activeChatTurnTimeoutRef.current = null;
      // 保留 answer：L3 可能在 compaction 后晚于本定时器返回，与 Lark 同源的最终包仍应写入气泡
      updateSessionMessagesById(turnSessionId, (prev) => {
        const last = prev[prev.length - 1];
        if (last?.role === "assistant" && !last.content?.trim() && !(last.reasoning ?? "").trim()) {
          return [
            ...prev.slice(0, -1),
            {
              ...last,
              content: `响应超时（${CHAT_RESPONSE_TIMEOUT_SEC} 秒），请检查 Layer 3 或 Layer 2 是否正常运行；长任务可在 .env 增大 VITE_CHAT_RESPONSE_TIMEOUT_MS`,
            },
          ];
        }
        return prev;
      });
      setIsLoading(false);
      setIsTyping(false);
    }, CHAT_RESPONSE_TIMEOUT_MS);
    activeChatTurnTimeoutRef.current = timeoutId;

    const intentForWire = content.trim() || (filesSnapshot.length > 0 ? "请查看附件并回答。" : "");
    const pendingL3InputRef = { current: intentForWire };
    registerChunkHandler(chunkHandler);
    registerStepHandler(stepHandler);
    registerAnswerHandler((answerContent, meta) => {
      if (myTurnToken !== chatTurnTokenRef.current) return;
      const safeAnswerContent = stripAssistantUiProtocol(stripDefaultSadEmojiSuffix(String(answerContent ?? "")));
      const rid = meta?.runId ?? "";
      if (rid && l3ActiveRunIdRef.current && !l3RunIdsSameTurn(rid, l3ActiveRunIdRef.current)) {
        voiceChatTraceIfActive("l3.answer_stale_ignored", {
          runId: rid,
          expectedRunId: l3ActiveRunIdRef.current,
        });
        console.debug("[Chat] 忽略陈旧 answer runId=%s 期望=%s", rid, l3ActiveRunIdRef.current);
        return;
      }
      voiceChatTraceIfActive("l3.answer", {
        runId: rid,
        hadStream: meta?.hadStreamChunks,
        outcome: meta?.terminalOutcome,
        answerPreview: truncChatTrace(String(answerContent ?? ""), 500),
        answerLen: typeof answerContent === "string" ? answerContent.length : 0,
        latencyMs: Date.now() - l3StartedAt,
        ui: voiceTraceUiRef.current,
      });
      clearReasoningPulse();
      registerChunkHandler(null);
      registerAnswerHandler(null);
      registerStepHandler(null);
      const voiceTraceOutcome = meta?.terminalOutcome === "error" || meta?.terminalOutcome === "rejected" ? "l3_error" : "ok";
      const endTraceAfterVoiceDrain = () => {
        void voiceOrchestrator.finishStream().finally(() => {
          if (getActiveVoiceChatTraceId()) {
            const diagnostics = getVoiceTurnDiagnosticsSnapshot();
            const appendedOverWs = sendVoiceDiagnosticsAppend(rid, diagnostics);
            if (!appendedOverWs) {
              void appendL3VoiceDiagnostics(rid, diagnostics, currentSessionIdRef.current);
            }
            endVoiceChatTrace(voiceTraceOutcome, {
              source: "L3",
              finalLen: safeAnswerContent.length,
              voiceDrain: true,
            });
          }
        });
      };
      if (useJvsCompanionVoice) {
        voiceCompanionDebug("chat.companion_answer", {
          runId: rid || "",
          hadStream: meta?.hadStreamChunks,
          len: (answerContent || "").length,
          outcome: meta?.terminalOutcome,
        });
        void emitCompanionL3ToHud({
          kind: "answer",
          content: safeAnswerContent,
          runId: rid || undefined,
          meta,
        });
        if (
          typeof answerContent === "string" &&
          shouldSpeakFinalAnswer(safeAnswerContent, meta)
        ) {
          void voiceOrchestrator.onL3Chunk(safeAnswerContent);
        }
        endTraceAfterVoiceDrain();
      } else if (chatJvsVoiceActiveRef.current && chatVoiceSpeakSentences > 0) {
        if (
          typeof answerContent === "string" &&
          shouldSpeakFinalAnswer(safeAnswerContent, meta)
        ) {
          void voiceOrchestrator.onL3Chunk(safeAnswerContent);
        }
        endTraceAfterVoiceDrain();
      }
      const isL3Error = typeof answerContent === "string" && (
        answerContent.includes("Ollama") || answerContent.includes("APIConnectionError") ||
        answerContent.includes("RuntimeError") || answerContent.includes("未配置 API Key")
      );
      if (isL3Error && pendingL3InputRef.current && l2Available) {
        voiceChatTraceIfActive("l3.l2_fallback", {
          reason: truncChatTrace(String(answerContent), 200),
          pendingInput: truncChatTrace(pendingL3InputRef.current, 200),
        });
        console.debug("[Chat] L3 返回错误，兜底 L2:", answerContent.slice(0, 80));
        streamChatMessage(pendingL3InputRef.current, (chunk) => chunkHandler(chunk), {
          signal: l2Abort.signal,
        })
          .then((fullText) => {
            registerAnswerHandler(null);
            registerStepHandler(null);
            cleanup(fullText, "L2");
          })
          .catch((e) => {
            registerAnswerHandler(null);
            registerStepHandler(null);
            if ((e as Error)?.name === "AbortError" || l2Abort.signal.aborted) {
              if (myTurnToken === chatTurnTokenRef.current) {
                setIsLoading(false);
                setIsTyping(false);
                setLocalStreamChunkKind(null);
                setState("idle");
              }
              return;
            }
            cleanup(`L2 兜底也失败：${(e as Error).message}`, "L2");
          });
      } else {
        // Server final answer always updates the main bubble; streaming is only a preview.
        const hadStream = meta?.hadStreamChunks ?? false;
        const hasServerFinal = typeof answerContent === "string" && answerContent.trim().length > 0;
        const useServerFinal = hadStream && hasServerFinal;
        const sentryVariant: SentryNotifyVariant =
          meta?.terminalOutcome === "rejected"
            ? "rejected"
            : meta?.terminalOutcome === "error"
              ? "error"
              : "answer";
        cleanup(safeAnswerContent, "L3", {
          skipContentUpdate: hadStream && !hasServerFinal,
          ttsUseFinalOnly: useServerFinal,
          sentryVariant,
        });
      }
    });

    // 优先 L3（L3 直连大模型，自有 API Key），未连接时兜底 L2
    const implicitSignals: Record<string, unknown> = { ...(opts?.extraImplicitSignals || {}) };
    if (voiceCompanionActiveRef.current || chatJvsVoiceActiveRef.current) {
      implicitSignals.desktop_companion = true;
      implicitSignals.source = "desktop_voice_companion";
      if (justBargedInRef.current) {
        implicitSignals.just_interrupted = true;
      }
      if (lastWakeUpAtRef.current > 0 && Date.now() - lastWakeUpAtRef.current <= 30_000) {
        implicitSignals.wake_triggered_recently = true;
      }
    }
    justBargedInRef.current = false;
    const hasImplicitSignals = Object.keys(implicitSignals).length > 0;
    const voiceDiagnostics = voiceCompanionActiveRef.current || chatJvsVoiceActiveRef.current
      ? getVoiceTurnDiagnosticsSnapshot()
      : null;
    const attExtras =
      attBuilt.items.length > 0 || hasImplicitSignals || voiceDiagnostics
        ? {
            attachments_metadata: attBuilt.items.length > 0 ? attBuilt.items : undefined,
            implicit_signals: hasImplicitSignals ? implicitSignals : undefined,
            voice_diagnostics: voiceDiagnostics ?? undefined,
          }
        : undefined;
    voiceChatTraceIfActive("l3.route_decision", {
      sensoryConnected: sensory.connected,
      l2Available,
      turnToken: myTurnToken,
      sessionId: turnSessionId,
      intentPreview: truncChatTrace(intentForWire, 300),
      hasAttachments: filesSnapshot.length > 0,
    });
    if (sensory.connected) {
      const l3Ok = sendInput(intentForWire, attExtras);
      if (l3Ok) {
        voiceChatTraceIfActive("l3.ws_send_ok", {
          turnToken: myTurnToken,
          sessionId: turnSessionId,
        });
        console.debug("[Chat] L3 直连发送成功 sensory.connected=true");
        return; // L3 已接收，将通过 WebSocket 流式返回
      }
      voiceChatTraceIfActive("l3.ws_send_fail", { note: "fallback L2" });
      console.debug("[Chat] L3 发送失败（可能 ws 未就绪），fallback L2");
    } else {
      voiceChatTraceIfActive("l3.ws_not_connected", { note: "fallback L2" });
      console.debug("[Chat] L2 兜底 sensory.connected=false");
    }

    // L2 兜底前：若为 BI 等 L3 专用意图，优先尝试 L3 HTTP agent/run（Sensory WS 未连时也能触发）
    const l3Answer = await tryL3AgentForIntent(intentForWire, attExtras);
    if (l3Answer != null && l3Answer.trim()) {
      voiceChatTraceIfActive("l3.http_agent_hit", {
        answerPreview: truncChatTrace(l3Answer, 400),
        answerLen: l3Answer.length,
      });
      console.debug("[Chat] L3 agent/run 命中 BI 意图，使用 L3 回复");
      clearTimeout(timeoutId);
      activeChatTurnTimeoutRef.current = null;
      cleanup(l3Answer, "L3");
      return;
    }

    // L2 兜底
    try {
      voiceChatTraceIfActive("l2.stream_start", {
        intentPreview: truncChatTrace(intentForWire, 300),
      });
      console.debug("[Chat] L2 streamChatMessage 开始");
      const fullText = await streamChatMessage(intentForWire, (chunk) => chunkHandler(chunk), {
        signal: l2Abort.signal,
      });
      if (myTurnToken !== chatTurnTokenRef.current) return;
      voiceChatTraceIfActive("l2.stream_ok", {
        answerPreview: truncChatTrace(fullText, 400),
        answerLen: fullText.length,
        latencyMs: Date.now() - l3StartedAt,
      });
      cleanup(fullText, "L2");
    } catch (e) {
      voiceChatTraceIfActive("l2.stream_fail", { error: (e as Error).message });
      console.debug("[Chat] L2 streamChatMessage 失败:", (e as Error).message);
      void maybeNotifyJachinAssistantDone(`打字请求失败：${(e as Error).message}`, "error");
      clearTimeout(timeoutId);
      activeChatTurnTimeoutRef.current = null;
      clearReasoningPulse();
      registerAnswerHandler(null);
      registerStepHandler(null);
      registerChunkHandler(null);
      if ((e as Error)?.name === "AbortError" || l2Abort.signal.aborted) {
        if (myTurnToken === chatTurnTokenRef.current) {
          setIsLoading(false);
          setIsTyping(false);
          setLocalStreamChunkKind(null);
          setState("idle");
        }
        return;
      }
      updateSessionMessagesById(turnSessionId, (prev) => {
        const updated = [...prev];
        const last = updated[updated.length - 1];
        if (last?.role === "assistant" && !last.content?.trim()) {
          updated[updated.length - 1] = {
            ...last,
            content: `打字请求失败：${(e as Error).message}。请确认 Layer 3 (ws://localhost:18981) 或 Layer 2 (http://localhost:18888) 已启动。`,
          };
        }
        return updated;
      });
      setIsLoading(false);
      setIsTyping(false);
    }
  };

  const buildVoiceAssistantCue = useCallback((
    source: "sim" | "hud" | "ptt" | "companion_quick_send",
  ): { text: string; reason: string } | null => {
    if (source === "companion_quick_send") {
      return { text: "收到。", reason: "voice_companion_quick_reply_ack" };
    }
    return null;
  }, []);

  const dispatchVoiceUtterance = useCallback(
    async (
      text: string,
      source: "sim" | "hud" | "ptt" | "companion_quick_send",
      sttTrace?: Partial<VoiceTranscriptionResult> & { source?: string },
    ) => {
      const t = stripDefaultSadEmojiSuffix(text.trim());
      if (!t) return;
      const activeTasks = Array.from(activeVoiceTasksRef.current.values());
      const activeTaskContext = activeTasks.length > 0
        ? {
            active_tasks: activeTasks.slice(0, 3).map((task) => ({
              id: task.id,
              title: task.title || "",
            })),
            focused_task_id: lastFocusVoiceTaskIdRef.current || activeTasks[0]?.id || null,
            summary: activeTasks[0]?.title
              ? `${activeTasks[0].title}${activeTasks.length > 1 ? `（另有${activeTasks.length - 1}个任务）` : ""}`
              : undefined,
            source: "desktop_voice_active_task_context",
          }
        : undefined;
      voiceCompanionDebug("chat.voice_text_to_l3", {
        source,
        text: truncVoiceLog(t, 100),
        rawText: truncVoiceLog(sttTrace?.rawText || t, 100),
        correctedText: truncVoiceLog(sttTrace?.correctedText || sttTrace?.text || t, 100),
        finalText: truncVoiceLog(sttTrace?.text || t, 100),
        streamText: truncVoiceLog(sttTrace?.streamText || "", 100),
        sttSource: sttTrace?.source || "",
        sttFinalized: sttTrace?.finalized,
        sttProvisional: sttTrace?.provisional,
        hotwordCount: sttTrace?.hotwordCount,
        hotwordStatus: sttTrace?.hotwordStatus,
        hotwordDominated: sttTrace?.hotwordDominated,
        hotwordDominationReasons: (sttTrace as any)?.hotwordDominationReasons,
        activeTaskCount: activeTasks.length,
        focusedTaskId: activeTaskContext?.focused_task_id || "",
      });
      const assistantCue = buildVoiceAssistantCue(source);
      await doActualSend(t, [], {
        displayContent: t,
        assistantCueText: assistantCue?.text,
        assistantCueReason: assistantCue?.reason,
        extraImplicitSignals: {
          desktop_companion: true,
          local_voice_session: true,
          voice_raw_stt_text: t,
          voice_asr_raw_text: sttTrace?.rawText || t,
          voice_corrected_text: sttTrace?.correctedText || sttTrace?.text || t,
          voice_final_text: sttTrace?.text || t,
          voice_stt_confidence: sttTrace?.confidence,
          voice_stt_backend: sttTrace?.backend,
          voice_stt_source: sttTrace?.source || "",
          voice_stt_finalized: sttTrace?.finalized,
          voice_stt_provisional: sttTrace?.provisional,
          voice_stt_stream_text: sttTrace?.streamText || "",
          voice_stt_duration_ms: sttTrace?.durationMs,
          voice_stt_hotword_count: sttTrace?.hotwordCount,
          voice_stt_hotword_status: sttTrace?.hotwordStatus,
          voice_stt_hotword_sources: sttTrace?.hotwordSources,
          voice_stt_hotword_dominated: sttTrace?.hotwordDominated,
          voice_stt_hotword_domination_reasons: (sttTrace as any)?.hotwordDominationReasons,
          voice_stt_understanding: sttTrace?.understanding,
          voice_active_task_context: activeTaskContext,
          source,
        },
      });
    },
    [buildVoiceAssistantCue, doActualSend],
  );

  // 语音陪伴：HUD / Orb / 模拟脚本注入用户文本 → chat 单一 L3 发送方
  useEffect(() => {
    let unlistenSim: (() => void) | undefined;
    let unlistenSend: (() => void) | undefined;
    let disposed = false;

    const onCompanionInject = (content: string, source: "sim" | "hud") => {
    const t = stripDefaultSadEmojiSuffix(content.trim());
      if (!t) return;
      voiceCompanionActiveRef.current = true;
      voiceCompanionDebug(`chat.companion_inject_${source}`, { content: truncVoiceLog(t, 120) });
      void dispatchVoiceUtterance(t, source);
    };

    void listen<{ content?: string }>("voice-sim-user-input", (ev) => {
      const content = typeof ev.payload?.content === "string" ? ev.payload.content : "";
      onCompanionInject(content, "sim");
    })
      .then((fn) => {
        if (disposed) fn();
        else unlistenSim = fn;
      })
      .catch(() => {});

    void listen<{ content?: string }>(VOICE_COMPANION_SEND_EVENT, (ev) => {
      const content = typeof ev.payload?.content === "string" ? ev.payload.content : "";
      onCompanionInject(content, "hud");
    })
      .then((fn) => {
        if (disposed) fn();
        else unlistenSend = fn;
      })
      .catch(() => {});

    return () => {
      disposed = true;
      unlistenSim?.();
      unlistenSend?.();
    };
  }, [dispatchVoiceUtterance]);

  useEffect(() => {
    let unlisten: (() => void) | undefined;
    const ensureHudTtsSession = (runId?: string) => {
      const key = runId?.trim() || "hud-ws";
      if (hudTtsBridgeRunRef.current === key) return;
      hudTtsBridgeRunRef.current = key;
      voiceCompanionActiveRef.current = true;
      voiceOrchestrator.startSession(`companion-hud-${key}-${Date.now()}`, {
        ttsVoice: resolveCompanionJvsVoice(ttsVoice),
      });
      startCompanionJvsIfNeeded();
      voiceOrchestrator.onL3Thinking();
      voiceCompanionDebug("chat.hud_tts_bridge_start", {
        runId: key,
        ttsVoiceRaw: ttsVoice ?? "",
        ttsVoiceResolved: resolveCompanionJvsVoice(ttsVoice),
      });
    };

    void listen<VoiceCompanionTtsPayload>(VOICE_COMPANION_TTS_EVENT, (ev) => {
      const p = ev.payload;
      if (!p?.kind) return;
      const runId = p.runId || p.meta?.runId || "";
      if (p.kind === "thinking") {
        ensureHudTtsSession(runId);
        return;
      }
      if (p.kind === "chunk") {
        const delta = typeof p.delta === "string" ? p.delta : "";
        if (!delta.trim() || p.chunkMeta?.isReasoning) return;
        ensureHudTtsSession(runId);
        void voiceOrchestrator.onL3Chunk(delta);
        return;
      }
      if (p.kind === "answer") {
        const content = typeof p.content === "string" ? p.content : "";
        ensureHudTtsSession(runId);
        if (!p.meta?.hadStreamChunks && content.trim() && p.meta?.terminalOutcome !== "error" && p.meta?.terminalOutcome !== "rejected") {
          void voiceOrchestrator.onL3Chunk(content);
        }
        void voiceOrchestrator.finishStream();
        hudTtsBridgeRunRef.current = "";
      }
    })
      .then((fn) => {
        unlisten = fn;
      })
      .catch(() => {});

    return () => {
      unlisten?.();
    };
  }, [startCompanionJvsIfNeeded, ttsVoice]);
  const handleVoiceBargeIn = useCallback(async () => {
    if (!voiceCompanionActiveRef.current && !companionModeRef.current) return;
    const now = Date.now();
    if (now - lastBargeInAtRef.current < 180) {
      voiceCompanionDebug("chat.barge_in_skipped_cooldown", {
        sinceMs: now - lastBargeInAtRef.current,
      });
      return;
    }
    lastBargeInAtRef.current = now;
    voiceCompanionDebug("chat.barge_in", {});
    typewriterCancelRef.current?.();
    chatTurnTokenRef.current += 1;
    if (activeChatTurnTimeoutRef.current != null) {
      clearTimeout(activeChatTurnTimeoutRef.current);
      activeChatTurnTimeoutRef.current = null;
    }
    l2StreamAbortRef.current?.abort();
    l2StreamAbortRef.current = null;
    await voiceOrchestrator.bargeIn();
    sendRunAbort();
    justBargedInRef.current = true;
    registerChunkHandler(null);
    registerAnswerHandler(null);
    registerStepHandler(null);
    setIsLoading(false);
    setIsTyping(false);
    setLocalStreamChunkKind(null);
    setRiskLevel("safe");
    setState("idle");
  }, [
    sendRunAbort,
    registerChunkHandler,
    registerAnswerHandler,
    registerStepHandler,
  ]);

  useEffect(() => {
    let unlisten: (() => void) | undefined;
    void listen<{ source?: string }>("voice-barge-in", (ev) => {
      voiceCompanionDebug("chat.barge_in_event", { source: ev.payload?.source ?? "" });
      void handleVoiceBargeIn();
    })
      .then((fn) => {
        unlisten = fn;
      })
      .catch(() => {});
    return () => {
      unlisten?.();
    };
  }, [handleVoiceBargeIn]);

  const handleStopGeneration = useCallback(() => {
    typewriterCancelRef.current?.();
    chatTurnTokenRef.current += 1;
    if (activeChatTurnTimeoutRef.current != null) {
      clearTimeout(activeChatTurnTimeoutRef.current);
      activeChatTurnTimeoutRef.current = null;
    }
    l2StreamAbortRef.current?.abort();
    l2StreamAbortRef.current = null;
    sendRunAbort();
    registerChunkHandler(null);
    registerAnswerHandler(null);
    registerStepHandler(null);
    setIsLoading(false);
    setIsTyping(false);
    setLocalStreamChunkKind(null);
    setRiskLevel("safe");
    setState("idle");
  }, [sendRunAbort, registerChunkHandler, registerAnswerHandler, registerStepHandler]);

  const handleSend = async () => {
    const t = input.trim();
    if ((!t && pendingFiles.length === 0) || isLoading || isTyping) return;
    await doActualSend(t, pendingFiles);
  };

  const handleConfirmHighRisk = () => {
    const pending = pendingHighRisk;
    setPendingHighRisk(null);
    setRiskLevel("safe");
    if (pending) doActualSend(pending.text);
  };

  const handleCancelHighRisk = () => {
    setPendingHighRisk(null);
    setRiskLevel("safe");
  };

  // 同步 VAD 状态与 Rust 引擎
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const running = await invoke<boolean>("is_voice_capture_running");
        if (!cancelled) setIsVadActive(running);
      } catch {
        if (!cancelled) setIsVadActive(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const handleVadToggle = async () => {
    try {
      if (isVadActive) {
        await invoke("stop_voice_capture");
        setIsVadActive(false);
        setRecordingStatus("");
      } else {
        await invoke("start_voice_capture");
        setIsVadActive(true);
        setRecordingStatus("VAD 已开启，正在监听…");
      }
    } catch (e) {
      setRecordingStatus(String(e));
    }
  };

  const clearPttAudioWaitTimer = useCallback(() => {
    if (pttAudioWaitTimerRef.current != null) {
      clearTimeout(pttAudioWaitTimerRef.current);
      pttAudioWaitTimerRef.current = null;
    }
  }, []);

  const handlePttCaptureFailed = useCallback(
    (detail: string, reason?: string) => {
      clearPttAudioWaitTimer();
      pttFinalizePendingRef.current = false;
      voiceChatTrace("ptt.no_audio", { reason, detail });
      endVoiceChatTrace("ptt_fail", { error: detail, reason });
      setRecordingStatus(`录音失败: ${detail}`);
      setState("idle");
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: `错误: ${detail}`, timestamp: Date.now() },
      ]);
    },
    [clearPttAudioWaitTimer, setMessages, setState],
  );

  const schedulePttAudioWaitTimeout = useCallback(() => {
    clearPttAudioWaitTimer();
    pttAudioWaitTimerRef.current = setTimeout(() => {
      if (!pttFinalizePendingRef.current) return;
      handlePttCaptureFailed(
        "未收到录音数据（超时）。若唤醒门卫刚关闭，请再试一次；并确认麦克风权限与默认输入设备。",
        "audio_wait_timeout",
      );
    }, 8000);
  }, [clearPttAudioWaitTimer, handlePttCaptureFailed]);

  const abortActiveStt = useCallback((reason: string, endTrace = false) => {
    const controller = activeSttAbortRef.current;
    if (!controller) return;
    voiceSttRequestSeqRef.current += 1;
    activeSttAbortRef.current = null;
    voiceChatTrace("stt.abort_active", {
      reason,
      requestSeq: voiceSttRequestSeqRef.current,
    });
    controller.abort();
    if (endTrace) {
      endVoiceChatTrace("cancel", {
        reason,
        stage: "stt_abort_active",
      });
    }
  }, []);

  const submitVoiceUtterance = useCallback(
    async (
      wavBase64: string,
      profile: "chat_ptt" | "chat_vad",
      preRecognizedText?: string,
      preRecognizedFinalized?: boolean,
      preRecognizedSource?: string,
    ) => {
      const fp = `${wavBase64.length}:${wavBase64.slice(0, 64)}:${wavBase64.slice(-64)}`;
      const now = Date.now();
      const last = lastVoiceSubmitRef.current;
      if (last && last.fp === fp && now - last.at < 6000) {
        voiceCompanionDebug("chat.voice_submit_dedup", {
          profile,
          sinceMs: now - last.at,
          wavBase64Len: wavBase64.length,
        });
        return;
      }
      lastVoiceSubmitRef.current = { fp, at: now };

      abortActiveStt("new_voice_submit");
      const sttRequestSeq = ++voiceSttRequestSeqRef.current;
      const sttAbortController = new AbortController();
      activeSttAbortRef.current = sttAbortController;

      if (profile === "chat_vad" && !getActiveVoiceChatTraceId()) {
        beginVoiceChatTrace("chat_vad", voiceTraceUiRef.current);
      }
      const wavBytes = wavBase64.length;
      voiceChatTrace("stt.audio_ready", {
        profile,
        wavBase64Len: wavBytes,
        approxWavBytes: Math.floor((wavBytes * 3) / 4),
        ui: voiceTraceUiRef.current,
      });
      try {
        setRecordingStatus("正在识别语音…");
        setState("thinking");
        clearPttAudioWaitTimer();
        const useCompanionUi = companionModeRef.current || voiceCompanionActiveRef.current;
        if (useCompanionUi) {
          voiceCompanionActiveRef.current = true;
          void armCompanionVoiceSession();
          void ensureCompanionSurfaceVisible();
          voiceCompanionDebug("chat.companion_ptt_surface_arm", { profile, stage: "before_final_stt" });
        }
        let wavForStt = wavBase64;
        let shouldRunPttOwnerTrack = false;
        if (useCompanionUi && profile === "chat_ptt") {
          try {
            const settings = await invoke<{
              speaker_verification_enabled?: boolean | null;
              speaker_verification_strict?: boolean | null;
              speaker_owner_track_enabled?: boolean | null;
            }>("get_user_settings");
            const fastOwnerTrackBypass =
              typeof localStorage === "undefined"
                ? true
                : localStorage.getItem("jachin.voice.companionOwnerTrackFastBypass") !== "false";
            shouldRunPttOwnerTrack = Boolean(
              settings?.speaker_verification_enabled !== false &&
                settings?.speaker_owner_track_enabled !== false &&
                (settings?.speaker_verification_strict === true || !fastOwnerTrackBypass),
            );
            if (
              settings?.speaker_verification_enabled !== false &&
              settings?.speaker_owner_track_enabled !== false &&
              settings?.speaker_verification_strict !== true &&
              fastOwnerTrackBypass
            ) {
              voiceChatTrace("sv.owner_track_ptt_fast_bypass", {
                profile,
                reason: "companion_fast_mode_non_strict",
              });
              voiceCompanionDebug("chat.sv_owner_track_ptt_fast_bypass", {
                reason: "companion_fast_mode_non_strict",
              });
            }
          } catch {
            shouldRunPttOwnerTrack = false;
          }
        }
        if (shouldRunPttOwnerTrack) {
          const svStarted = Date.now();
          try {
            const sv = await invoke<{
              accepted: boolean;
              used_owner_track: boolean;
              wav_base64?: string | null;
              reason: string;
              owner_duration_ms?: number | null;
              skipped_segments_count?: number | null;
            }>("companion_filter_owner_track_wav", {
              wavBase64,
            });
            voiceChatTrace("sv.owner_track_ptt", {
              profile,
              accepted: sv.accepted,
              usedOwnerTrack: sv.used_owner_track,
              reason: sv.reason,
              ownerDurationMs: sv.owner_duration_ms ?? null,
              skippedSegmentsCount: sv.skipped_segments_count ?? null,
              latencyMs: Date.now() - svStarted,
            });
            voiceCompanionDebug("chat.sv_owner_track_ptt", {
              accepted: sv.accepted,
              usedOwnerTrack: sv.used_owner_track,
              reason: sv.reason,
              ownerDurationMs: sv.owner_duration_ms ?? null,
              skippedSegmentsCount: sv.skipped_segments_count ?? null,
            });
            if (!sv.accepted) {
              endVoiceChatTrace("stt_fail", { error: sv.reason, profile, stage: "sv_owner_track" });
              setRecordingStatus("未识别到主人的声音，请重试。");
              setState("idle");
              return;
            }
            const filtered = (sv.wav_base64 || "").trim();
            if (filtered) {
              wavForStt = filtered;
            }
          } catch (err) {
            const msg = err instanceof Error ? err.message : String(err);
            voiceChatTrace("sv.owner_track_ptt_fail_open", {
              profile,
              error: msg,
            });
            voiceCompanionDebug("chat.sv_owner_track_ptt_fail_open", {
              error: truncVoiceLog(msg, 200),
            });
          }
        }
        const sttStarted = Date.now();
        const streamText = stripDefaultSadEmojiSuffix((preRecognizedText || "").trim());
        if (streamText) {
          voiceChatTrace("stt.stream_preview_available", {
            profile,
            streamText,
            finalized: Boolean(preRecognizedFinalized),
            source: preRecognizedSource || "",
          });
        }
        const useRealtimeFinal =
          profile === "chat_ptt" &&
          Boolean(streamText) &&
          preRecognizedFinalized === true &&
          !shouldRunPttOwnerTrack;
        const useLocalFirstFallback = profile === "chat_ptt" && !useRealtimeFinal;
        const finalTrace = useRealtimeFinal
          ? ({
              text: streamText,
              rawText: streamText,
              correctedText: streamText,
              confidence: 0.92,
              durationMs: 0,
              language: "auto",
              backend: preRecognizedSource || "jvs_stream_final",
              hotwordCount: 0,
              hotwordStatus: "streaming",
              hotwordSources: [],
              understanding: {
                streamingMode: "ptt_realtime_stt",
                source: preRecognizedSource || "jvs_stream_final",
              },
            } as VoiceTranscriptionResult)
          : await transcribeWavBase64Detailed(wavForStt, profile, {
              signal: sttAbortController.signal,
              localFirst: useLocalFirstFallback,
            });
        if (sttAbortController.signal.aborted || sttRequestSeq !== voiceSttRequestSeqRef.current) {
          voiceChatTrace("stt.pipeline_superseded", {
            profile,
            requestSeq: sttRequestSeq,
            currentRequestSeq: voiceSttRequestSeqRef.current,
          });
          return;
        }
        const sttTrace: Partial<VoiceTranscriptionResult> & { source: string } = {
          ...finalTrace,
          source: useRealtimeFinal ? "jvs_stream_final" : (useLocalFirstFallback ? "jvs_local_fallback" : "jvs_http_transcribe"),
          finalized: true,
          provisional: false,
          streamText,
        };
        sttTrace.hotwordDominated = false;
        (sttTrace as Partial<VoiceTranscriptionResult> & { hotwordDominationReasons?: string[] }).hotwordDominationReasons = [];
        const text = sttTrace.text || "";
        voiceChatTrace("stt.recognized", {
          profile,
          text,
          rawText: sttTrace.rawText || text,
          correctedText: sttTrace.correctedText || text,
          confidence: sttTrace.confidence,
          backend: sttTrace.backend,
          durationMs: sttTrace.durationMs,
          hotwordCount: sttTrace.hotwordCount,
          hotwordStatus: sttTrace.hotwordStatus,
          hotwordSources: sttTrace.hotwordSources,
          hotwordDominated: sttTrace.hotwordDominated,
          hotwordDominationReasons: (sttTrace as any).hotwordDominationReasons,
          latencyMs: Date.now() - sttStarted,
          source: useRealtimeFinal ? "jvs_stream_final" : (useLocalFirstFallback ? "jvs_local_fallback" : "jvs_http_transcribe"),
          finalized: true,
          provisional: false,
          streamText,
          streamFinalChanged: Boolean(streamText && streamText !== text),
        });
        chatJvsVoiceActiveRef.current = true;
        startCompanionJvsIfNeeded();
        setRecordingStatus("");
        const wireText = formatVoiceUserMessage(text, profile);
        const recognizedText = stripDefaultSadEmojiSuffix((text || "").trim());
        // 陪伴态语音按钮：将识别文本写入临时交互框，并走陪伴语音会话链路。
        if (useCompanionUi) {
          voiceCompanionActiveRef.current = true;
          void armCompanionVoiceSession();
          if (recognizedText) {
            void emitCompanionUserToHud(recognizedText);
          }
        }
        const sendText = useCompanionUi ? (recognizedText || wireText) : wireText;
        voiceChatTrace("l3.send_start", {
          profile,
          recognizedText: text,
          wireText: sendText,
          ttsEnabled,
          // 语音按钮输入始终朗读（不受全局 ttsEnabled 影响）
          maxSpeakSentences: VOICE_PROFILES.chat_ptt.maxSpeakSentences,
          companionUi: useCompanionUi,
          ui: voiceTraceUiRef.current,
        });
        await dispatchVoiceUtterance(sendText, "ptt", sttTrace);
      } catch (e) {
        const msg = e instanceof VoiceServiceError ? e.message : VOICE_UNAVAILABLE_HINT;
        const voiceErrorDetails = e instanceof VoiceServiceError && e.details ? e.details : {};
        const voiceErrorReason = String((voiceErrorDetails as Record<string, unknown>).reason || "");
        if (
          voiceErrorReason === "jvs_stt_aborted" ||
          sttAbortController.signal.aborted ||
          sttRequestSeq !== voiceSttRequestSeqRef.current
        ) {
          voiceChatTrace("stt.pipeline_superseded", {
            profile,
            reason: voiceErrorReason || "request_aborted_or_stale",
            requestSeq: sttRequestSeq,
            currentRequestSeq: voiceSttRequestSeqRef.current,
          });
          return;
        }
        voiceChatTrace("stt.pipeline_fail", {
          profile,
          error: msg,
          code: e instanceof VoiceServiceError ? e.code : "unknown",
        });
        endVoiceChatTrace("stt_fail", { error: msg });
        setMessages((prev) => [
          ...prev,
          { role: "assistant", content: `错误: ${msg}`, timestamp: Date.now() },
        ]);
        setRecordingStatus(`错误: ${msg}`);
        setState("idle");
        chatJvsVoiceActiveRef.current = false;
      } finally {
        if (activeSttAbortRef.current === sttAbortController) {
          activeSttAbortRef.current = null;
        }
      }
    },
    [dispatchVoiceUtterance, setMessages, startCompanionJvsIfNeeded, setState, ttsEnabled, clearPttAudioWaitTimer, abortActiveStt, ensureCompanionSurfaceVisible],
  );

  useSttAudioReady({
    playOnReady: false,
    onReady: (payload) => {
      if (!payload?.wav_base64) return;
      if (pttFinalizePendingRef.current || isRecordingRef.current) {
        clearPttAudioWaitTimer();
        pttFinalizePendingRef.current = false;
        void submitVoiceUtterance(
          payload.wav_base64,
          "chat_ptt",
          payload.recognized_text,
          payload.recognized_finalized,
          payload.recognized_source,
        );
        prewarmPttSttStream("ptt_audio_ready_next_turn");
        return;
      }
      if (!isVadActiveRef.current) return;
      void submitVoiceUtterance(payload.wav_base64, "chat_vad");
    },
    onPttFailed: (payload) => {
      if (!pttFinalizePendingRef.current && !isRecordingRef.current) return;
      handlePttCaptureFailed(payload.detail || payload.reason, payload.reason);
    },
  });

  /** 大窗语音：点击开始录音，点「结束」截句送 JVS STT（Voice Core） */
  const startRecording = async () => {
    if (isRecordingRef.current) return;
    abortActiveStt("new_ptt_start", true);
    beginVoiceChatTrace("chat_ptt", voiceTraceUiRef.current);
    voiceChatTrace("ptt.start_click", { ui: voiceTraceUiRef.current });
    getCurrentWindow().setFocus().catch(() => {});
    setListeningText("");
    pttFinalizePendingRef.current = false;
    clearPttAudioWaitTimer();
    try {
      // Pre-flight: 麦克风刚起录即通知 L3 后台预热历史/摘要。
      sendPrepareContextControl("ptt_start");
      prewarmPttSttStream("ptt_start");
      // start_ptt_capture 内会先 stop 唤醒门卫并等待麦克风释放
      await invoke("start_ptt_capture");
      isRecordingRef.current = true;
      setIsRecording(true);
      setRecordingStatus("录音中…点击「结束」发送");
      setState("listening");
      voiceChatTrace("ptt.rust_capture_started", { ui: voiceTraceUiRef.current });
    } catch (error: unknown) {
      isRecordingRef.current = false;
      pttFinalizePendingRef.current = false;
      setIsRecording(false);
      setListeningText("");
      const msg = error instanceof Error ? error.message : String(error);
      const display = msg.includes("ambient") ? "语音采集未编译：请启用 ambient 特性构建" : msg;
      voiceChatTrace("ptt.rust_start_fail", { error: msg, display });
      endVoiceChatTrace("ptt_fail", { error: msg });
      setRecordingStatus(display);
      setState("idle");
    }
  };

  const stopRecording = async () => {
    if (!isRecordingRef.current && !pttFinalizePendingRef.current) return;
    voiceChatTrace("ptt.stop_click", {
      wasRecording: isRecordingRef.current,
      finalizePending: pttFinalizePendingRef.current,
      ui: voiceTraceUiRef.current,
    });
    isRecordingRef.current = false;
    setIsRecording(false);
    setListeningText("");
    pttFinalizePendingRef.current = true;
    setRecordingStatus("正在识别…");
    try {
      const payload = await invoke<{
        wav_base64: string;
        recognized_text?: string;
        recognized_finalized?: boolean;
        recognized_source?: string;
      }>("stop_ptt_capture");
      voiceChatTrace("ptt.rust_capture_stopped", {
        ui: voiceTraceUiRef.current,
        wavBase64Len: payload?.wav_base64?.length ?? 0,
        via: "invoke",
      });
      clearPttAudioWaitTimer();
      pttFinalizePendingRef.current = false;
      if (payload?.wav_base64) {
        void submitVoiceUtterance(
          payload.wav_base64,
          "chat_ptt",
          payload.recognized_text,
          payload.recognized_finalized,
          payload.recognized_source,
        );
        prewarmPttSttStream("ptt_stop_next_turn");
      } else {
        schedulePttAudioWaitTimeout();
      }
    } catch (error: unknown) {
      const msg = error instanceof Error ? error.message : String(error);
      voiceChatTrace("ptt.rust_stop_fail", { error: msg });
      if (msg.includes("未收到录音数据（超时）")) {
        // Rust stop_ptt_capture 可能先返回超时，随后才到 STT_AUDIO_READY；保留 pending 等事件兜底。
        pttFinalizePendingRef.current = true;
        setRecordingStatus("正在等待录音数据…");
        schedulePttAudioWaitTimeout();
        return;
      }
      pttFinalizePendingRef.current = false;
      if (msg.includes("未收到录音数据") || msg.includes("录音过短") || msg.includes("麦克风")) {
        handlePttCaptureFailed(msg, "rust_no_audio");
      } else {
        endVoiceChatTrace("ptt_fail", { error: msg });
        setRecordingStatus(`停止录音失败: ${msg}`);
        setState("idle");
      }
    }
  };

  // v8.0 人格色彩：Handoff 时全局主题突变（default 科技蓝 / architect 赛博紫 / researcher 矩阵绿）
  const personaTheme = handoffEvent?.persona ?? "default";
  useEffect(() => {
    const root = document.getElementById("chat-root");
    if (root) root.setAttribute("data-persona", personaTheme);
  }, [personaTheme]);

  /** 仅录音 / VAD / TTS 时用声波动画。等待模型时仍保留文本框（否则会卸载 input，表现为「无法输入」）；思考态由 Jachin Core 的 cyberPhase 呈现 */
  const interactionPhase: "text" | WavePhase = ttsPlaying
    ? "speaking"
    : isRecording || isVadActive
      ? "mic_listen"
      : "text";

  const openConsole = () => {
    void invoke("show_console_window");
    setCompanionMode((p) => (p ? p : true));
    void invoke<HideChatWindowResult>("hide_chat_window")
      .then((r) => setCompanionMode(Boolean(r?.companion)))
      .catch((err) => {
        console.error("[Omni] hide_chat_window (openConsole):", err);
        setCompanionMode(false);
      });
  };

  const lastAssistantBubble = [...messages].reverse().find((m) => m.role === "assistant");

  /**
   * 与气泡内 Thought Process / 正文严格同步，显式传入 JachinCore（避免 streamDisplay 含 reasoning 却被当成 STREAMING）。
   */
  const jachinMachineState = useMemo((): JachinCoreMachineState => {
    if (jachinCore.selfHealFlash) return "IDLE";
    if (sensory.hitlPending) return "IDLE";
    const last = lastAssistantBubble;
    const r = last?.role === "assistant" ? (last.reasoning ?? "").trim() : "";
    const c = last?.role === "assistant" ? (last.content ?? "").trim() : "";
    if (isTyping && streamChunkKindEffective === "reasoning") return "THINKING";
    if (isTyping && r.length > 0 && c.length === 0) return "THINKING";
    if (isTyping && (r.length > 0 || c.length > 0)) return "STREAMING";
    if (isLoading && !isRecording && !isVadActive) return "THINKING";
    if (jachinCore.coreState === "thinking") return "THINKING";
    if (jachinCore.coreState === "streaming" && (r.length > 0 || c.length > 0)) return "STREAMING";
    return "IDLE";
  }, [
    jachinCore.selfHealFlash,
    jachinCore.coreState,
    sensory.hitlPending,
    isTyping,
    isLoading,
    isRecording,
    isVadActive,
    streamChunkKindEffective,
    lastAssistantBubble?.role,
    lastAssistantBubble?.reasoning,
    lastAssistantBubble?.content,
  ]);

  useEffect(() => {
    voiceTraceUiRef.current = {
      machineState: jachinMachineState,
      recordingStatus,
      isRecording,
      isVadActive,
      isLoading,
      isTyping,
      ttsPlaying,
      ttsEnabled,
      sensoryConnected: sensory.connected,
      l2Available,
      companionMode,
      sessionId: currentSessionId,
    };
  }, [
    jachinMachineState,
    recordingStatus,
    isRecording,
    isVadActive,
    isLoading,
    isTyping,
    ttsPlaying,
    ttsEnabled,
    sensory.connected,
    l2Available,
    companionMode,
    currentSessionId,
  ]);

  /** 赛博壳层相位（与 jachinMachineState 一致，供主题/其它 UI） */
  const cyberPhase = useMemo((): CorePhase => {
    if (jachinCore.selfHealFlash) return CorePhase.HEALING;
    if (sensory.hitlPending) return CorePhase.THINKING;
    if (jachinMachineState === "THINKING") return CorePhase.THINKING;
    if (jachinMachineState === "STREAMING") return CorePhase.STREAMING;
    return CorePhase.IDLE;
  }, [jachinCore.selfHealFlash, sensory.hitlPending, jachinMachineState]);

  /** 右侧画布：最近一条未解决的 canvas 模式 tool_call */
  const activeSkillCanvas = useMemo(() => getActiveSkillCanvasFromMessages(messages), [messages]);

  /** 画布激活：扩窗；关画布不 restore，避免左栏 flex 拉满吞掉原画布区（右侧保留空列） */
  useLayoutEffect(() => {
    if (!activeSkillCanvas) return;
    let cancelled = false;
    void (async () => {
      const again = async () => {
        await expandChatWindowForSkillCanvas();
        if (cancelled) return;
        await new Promise<void>((r) => requestAnimationFrame(() => r()));
        if (cancelled) return;
        await expandChatWindowForSkillCanvas();
        if (cancelled) return;
        await new Promise<void>((r) => requestAnimationFrame(() => r()));
        if (cancelled) return;
        await expandChatWindowForSkillCanvas();
      };
      await again();
    })();
    return () => {
      cancelled = true;
    };
  }, [activeSkillCanvas]);

  const handleDismissSkillCanvas = useCallback(() => {
    setMessages((prev) => {
      const active = getActiveSkillCanvasFromMessages(prev);
      if (!active) return prev;
      return dismissUnresolvedToolCallMessage(prev, active);
    });
  }, []);

  const companionAiState = useMemo<AiState>(() => {
    if (hudOrbState) return hudOrbState;
    if (isRecording || isVadActive) return "listening";
    if (isTyping || isLoading || jachinMachineState === "THINKING") return "thinking";
    if (ttsPlaying || jachinMachineState === "STREAMING") return "speaking";
    return "idle";
  }, [hudOrbState, isRecording, isVadActive, isTyping, isLoading, jachinMachineState, ttsPlaying]);

  const handleCompanionVoiceStart = useCallback(() => {
    voiceCompanionActiveRef.current = true;
    void armCompanionVoiceSession();
    sendPrepareContextControl("companion_voice_start");
    void (async () => {
      if (isLoading || isTyping || ttsPlaying) {
        await handleVoiceBargeIn();
        await new Promise<void>((r) => setTimeout(() => r(), 120));
      }
      await startRecording();
    })();
  }, [
    armCompanionVoiceSession,
    handleVoiceBargeIn,
    isLoading,
    isTyping,
    sendPrepareContextControl,
    startRecording,
    ttsPlaying,
  ]);

  const handleCompanionVoiceStop = useCallback(() => {
    if (!isRecording) return;
    void stopRecording();
  }, [isRecording, stopRecording]);

  const handleCompanionQuickSend = useCallback(
    (text: string) => {
      if (!text.trim() || isLoading || isTyping) return;
      voiceCompanionActiveRef.current = true;
      void armCompanionVoiceSession();
      void emitCompanionUserToHud(text.trim());
      void dispatchVoiceUtterance(text.trim(), "companion_quick_send");
    },
    [armCompanionVoiceSession, dispatchVoiceUtterance, isLoading, isTyping],
  );

  return (
    <div
      className={`relative flex h-full w-full min-h-0 flex-col bg-transparent ${
        companionMode ? "items-center justify-start overflow-visible" : "overflow-hidden"
      }`}
    >
      <AnimatePresence>
        {companionMode ? (
          <CompanionOverlay
            state={companionAiState}
            isRecording={isRecording}
            onExpandFull={requestExpandFromSpark}
            onBargeIn={() => void handleVoiceBargeIn()}
            onVoiceStart={handleCompanionVoiceStart}
            onVoiceStop={handleCompanionVoiceStop}
            onQuickSend={handleCompanionQuickSend}
          />
        ) : (
          <motion.div
            key="omni-main"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2, ease: "easeOut" }}
            className="relative flex h-full min-h-0 w-full flex-col overflow-hidden bg-slate-950"
            onDragEnter={(e) => {
              e.preventDefault();
              e.stopPropagation();
              if (isLikelyExternalFileDrag(e.dataTransfer)) setOmniFileDragHighlight(true);
            }}
            onDragLeave={(e) => {
              e.preventDefault();
              e.stopPropagation();
              const root = e.currentTarget as HTMLElement;
              const rel = e.relatedTarget as Node | null;
              if (rel && root.contains(rel)) return;
              setOmniFileDragHighlight(false);
            }}
            onDragOver={(e) => {
              e.preventDefault();
              e.stopPropagation();
              const dt = e.dataTransfer;
              if (dt) {
                dt.dropEffect = "copy";
                if (isLikelyExternalFileDrag(dt)) setOmniFileDragHighlight(true);
              }
            }}
            onDrop={(e) => {
              e.preventDefault();
              e.stopPropagation();
              setOmniFileDragHighlight(false);
              const fl = e.dataTransfer?.files;
              if (fl?.length) mergeOmniPendingFiles(Array.from(fl));
            }}
          >
      <OmniTacticalVoidDecor />
      <WindowResizeHandles />
      {/* v8.0 全息感官：Handoff + Swarm + HITL 等 */}
      <SensoryOverlay sensory={sensory} variant="minimal" />
      <div className="pointer-events-none flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        <div className="relative z-10 flex h-full min-h-0 flex-1 flex-col overflow-hidden pointer-events-auto">
          <OmniDynamicHud
            expanded={omniHudExpanded}
            onExpandedChange={setOmniHudExpanded}
            backgroundTaskPulse={backgroundTaskPulse}
            zombieTasksPending={zombieTasksPending}
            dismissZombieTasksPending={dismissZombieTasksPending}
            memoryCompactSuggest={memoryCompactSuggest}
            sendMemoryCompactControl={sendMemoryCompactControl}
            dismissMemoryCompactSuggest={dismissMemoryCompactSuggest}
            setInput={setInput}
          />
          {/* 无画布：单栏 Omni 随窗口伸缩；有画布：左固定宽 + 右画布（与扩窗命令隔离，仅靠 activeSkillCanvas 切换布局） */}
          <div className="flex h-full min-h-0 w-full flex-1 flex-row items-stretch overflow-hidden">
            <div
              className={`relative flex min-h-0 flex-col overflow-hidden ${
                activeSkillCanvas ? "shrink-0" : "min-w-0 flex-1"
              }`}
              style={
                activeSkillCanvas
                  ? {
                      width: SKILL_CHAT_COLUMN_WIDTH,
                      maxWidth: "100%",
                      flex: "0 0 auto",
                    }
                  : undefined
              }
            >
              <OmniCyberChatShell
                phase={cyberPhase}
                jachinMachineState={jachinMachineState}
                thinkingToolFlash={jachinCore.toolFlash}
                messages={messages}
                input={input}
                onInputChange={setInput}
                onRequestDismiss={requestHideChat}
                onSend={handleSend}
                pendingFiles={pendingFiles}
                onMergePendingFiles={mergeOmniPendingFiles}
                onRemovePendingFile={removeOmniPendingFile}
                attachmentHint={attachmentHint}
                dragOverlayActive={omniFileDragHighlight}
                placeholder={
                  sensory.connected
                    ? desktopUi.placeholderL3
                    : l2Available
                      ? desktopUi.placeholderL2
                      : desktopUi.placeholderWait
                }
                ui={desktopUi}
                disabled={false}
                voiceBackendOk={sensory.connected || l2Available}
                isLoading={isLoading}
                isTyping={isTyping}
                isRecording={isRecording}
                onVoiceStart={startRecording}
                onVoiceStop={stopRecording}
                isVadActive={isVadActive}
                onVadToggle={handleVadToggle}
                interactionPhase={interactionPhase}
                micLevel={micLevel}
                onOpenConsole={openConsole}
                recordingStatus={recordingStatus}
                listeningText={listeningText}
                hitlPending={sensory.hitlPending}
                onHitlResolve={(ok) => sensory.resolveHitl(ok)}
                riskLevel={riskLevel}
                onToolUiResult={handleToolUiResult}
                onStopGeneration={handleStopGeneration}
                onNewChat={handleNewChat}
                sessionDrawerOpen={sessionDrawerOpen}
                onToggleSessionDrawer={() => setSessionDrawerOpen((o) => !o)}
                sessionsList={sessions.map((s) => ({
                  id: s.id,
                  title: sessionSidebarDisplayLabel(s),
                }))}
                currentSessionId={currentSessionId}
                onSelectSession={handleSelectSession}
                onDeleteSession={handleDeleteSession}
                devToolbar={
                  import.meta.env.DEV ? (
                    <details className="group relative z-40">
                      <summary className="cursor-pointer list-none rounded border border-white/10 bg-slate-950/40 px-1.5 py-0.5 text-[9px] font-mono text-slate-500 hover:border-cyan-500/30 hover:text-slate-300 [&::-webkit-details-marker]:hidden">
                        Dev
                      </summary>
                      <div className="absolute right-0 top-[calc(100%+6px)] z-50 w-max min-w-[9rem] rounded-lg border border-white/12 bg-slate-950/95 p-2 shadow-xl backdrop-blur-md">
                        <p className="mb-1.5 text-[9px] text-slate-500">注入 tool_call（联调）</p>
                        <div className="flex flex-wrap gap-1">
                          <button
                            type="button"
                            className="rounded border border-violet-500/40 px-2 py-0.5 text-[10px] text-violet-200 hover:bg-violet-950/80"
                            onClick={() => {
                              void (async () => {
                                await expandChatWindowForSkillCanvas();
                                await new Promise<void>((r) => requestAnimationFrame(() => r()));
                                await expandChatWindowForSkillCanvas();
                                setMessages((prev) => [...prev, createDemoComposeEssaySkillUiMessage()]);
                              })();
                            }}
                          >
                            作文
                          </button>
                          <button
                            type="button"
                            className="rounded border border-amber-500/40 px-2 py-0.5 text-[10px] text-amber-200 hover:bg-amber-950/80"
                            onClick={() => {
                              void (async () => {
                                await expandChatWindowForSkillCanvas();
                                await new Promise<void>((r) => requestAnimationFrame(() => r()));
                                await expandChatWindowForSkillCanvas();
                                setMessages((prev) => [...prev, createDemoGeneratePptSkillUiMessage()]);
                              })();
                            }}
                          >
                            PPT
                          </button>
                        </div>
                      </div>
                    </details>
                  ) : undefined
                }
              />
            </div>
            {activeSkillCanvas ? (
              <div className="relative flex min-h-0 min-w-[280px] flex-1 flex-col overflow-hidden bg-gradient-to-r from-cyan-500/[0.06] via-transparent to-transparent shadow-[inset_12px_0_24px_-8px_rgba(34,211,238,0.12)]">
                <SkillCanvasPane
                  key={`${activeSkillCanvas.toolCallId ?? ""}-${activeSkillCanvas.toolName}`}
                  active={activeSkillCanvas}
                  onToolUiResult={handleToolUiResult}
                  onRequestClose={handleDismissSkillCanvas}
                />
              </div>
            ) : null}
          </div>
        </div>
      </div>
          </motion.div>
        )}
      </AnimatePresence>
      {/* 高风险操作二次确认弹窗 */}
      {!companionMode && pendingHighRisk && (
        <div className="absolute inset-0 z-50 flex items-center justify-center bg-black/50 rounded-2xl" onClick={handleCancelHighRisk}>
          <div className="bg-slate-900 border-2 border-red-500/80 rounded-xl p-4 max-w-sm shadow-xl" onClick={(e) => e.stopPropagation()}>
            <p className="text-red-200 text-sm font-medium mb-2">⚠️ 检测到高风险操作</p>
            <p className="text-slate-300 text-xs mb-4">{pendingHighRisk.strippedText || "系统指令"}</p>
            <p className="text-slate-400 text-xs mb-4">请确认或口述确认码 <span className="text-cyan-400 font-mono">Alpha-9</span> 以继续。</p>
            <div className="flex gap-2 justify-end">
              <button type="button" onClick={handleCancelHighRisk} className="px-3 py-1.5 rounded-lg bg-white/10 text-slate-300 text-sm hover:bg-white/20">取消</button>
              <button type="button" onClick={handleConfirmHighRisk} className="px-3 py-1.5 rounded-lg bg-red-500/30 text-red-200 text-sm hover:bg-red-500/50 border border-red-400/50">确认</button>
            </div>
          </div>
        </div>
      )}
      <audio
        ref={bindChatAudio}
        style={{ display: "none" }}
        onPlay={() => setTtsPlaying(true)}
        onPause={() => setTtsPlaying(false)}
        onEnded={() => setTtsPlaying(false)}
      />
    </div>
  );
}

// 初始化：只 createRoot 一次，热更新时只调用 render，避免 “container already passed to createRoot” 警告
const rootEl = document.getElementById("chat-root");
if (rootEl) {
  const root =
    (window as Window & { __chatReactRoot?: ReturnType<typeof ReactDOM.createRoot> }).__chatReactRoot ??
    (() => {
      const r = ReactDOM.createRoot(rootEl);
      (window as Window & { __chatReactRoot?: typeof r }).__chatReactRoot = r;
      return r;
    })();
  root.render(
    <React.StrictMode>
      <ChatApp />
    </React.StrictMode>
  );
}
