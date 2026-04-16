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
import { listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { invoke } from "@tauri-apps/api/core";
import { voiceChat, synthesizeSpeech, voiceProcess, streamChatMessage, tryL3AgentForIntent, checkHealth, type VoiceProcessResponse } from "./lib/api";
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
import { desktopDiagLog } from "./lib/desktopDiagLog";
import { mergeStreamChunk } from "./utils/streamChunkMerge";
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
import { OmniMiniSpark } from "./components/Omni/OmniMiniSpark";
import { SensoryOverlay } from "./console/components/SensoryOverlay";
import { useJachinCoreState } from "./hooks/useJachinCoreState";
import { useDesktopUiLang } from "./hooks/useDesktopUiLang";
import { getDesktopOmniUi } from "./utils/desktopUiI18n";
import "./styles/globals.css";

/** 与 Rust `HideChatWindowResult` 对齐（camelCase） */
type HideChatWindowResult = { companion: boolean; fullyHidden: boolean };

function blobToBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => {
      const dataUrl = r.result as string;
      const base64 = dataUrl.split(",")[1];
      resolve(base64 ?? "");
    };
    r.onerror = () => reject(new Error("Blob read failed"));
    r.readAsDataURL(blob);
  });
}

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
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const speechRecognitionRef = useRef<SpeechRecognition | null>(null);
  const chatAudioRef = useRef<HTMLAudioElement | null>(null);
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
    sendToolUiResult,
    sendSessionClearControl,
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
  /** Lark 镜像回调：用 ref 读加载态，避免 effect 随 isLoading 反复卸载/重挂载导致抢答/丢 chunk */
  const isLoadingRef = useRef(false);
  const isTypingRef = useRef(false);
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
  /** 右下角陪伴圆模式（窗口缩小，非完全 hide） */
  const [companionMode, setCompanionMode] = useState(false);
  const companionModeRef = useRef(companionMode);
  useEffect(() => {
    companionModeRef.current = companionMode;
  }, [companionMode]);

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
      if (ev.event !== "completed" && ev.event !== "failed" && ev.event !== "cancelled") {
        return;
      }
      const taskId = ev.task_id;
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

  /** 陪伴态 React state 每次变化时打一条，与 Rust emit / 窗口 API 对照 */
  useEffect(() => {
    void (async () => {
      try {
        const w = getCurrentWindow();
        const [min, vis] = await Promise.all([w.isMinimized(), w.isVisible()]);
        void desktopDiagLog("react_companion_mode_state", {
          companionModeReact: companionMode,
          label: w.label,
          minimized: min,
          visible: vis,
        });
      } catch (e) {
        void desktopDiagLog("react_companion_mode_state_err", { err: String(e) });
      }
    })();
  }, [companionMode]);

  /**
   * 启动时同步 Rust 陪伴态。若该 invoke 较慢，用户可能已先按 Esc 坍缩；
   * 后到的 false 会错误盖掉 true → UI 与大窗不同步、球「随机」才出现。
   * 规则：本地已是陪伴态时，不再被这次启动同步降成 false。
   */
  useEffect(() => {
    let cancelled = false;
    void invoke<boolean>("is_chat_companion_mode")
      .then((v) => {
        if (cancelled) return;
        void desktopDiagLog("react_startup_companion_sync", {
          rustCompanionReported: v,
          note: "before merge with optimistic local state",
        });
        setCompanionMode((prev) => (prev ? prev : Boolean(v)));
      })
      .catch((e) => {
        void desktopDiagLog("react_startup_companion_sync_err", { err: String(e) });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let unlisten: (() => void) | undefined;
    listen<{ companion: boolean }>("omni-companion-mode", (ev) => {
      const next = Boolean(ev.payload?.companion);
      setCompanionMode(next);
      void (async () => {
        try {
          const w = getCurrentWindow();
          const [min, vis, focused] = await Promise.all([
            w.isMinimized(),
            w.isVisible(),
            w.isFocused().catch(() => false),
          ]);
          void desktopDiagLog("react_omni_companion_event", {
            payloadCompanion: next,
            label: w.label,
            minimized: min,
            visible: vis,
            focused,
          });
        } catch (e) {
          void desktopDiagLog("react_omni_companion_event_err", { err: String(e) });
        }
      })();
    })
      .then((fn) => {
        unlisten = fn;
      })
      .catch((err) => {
        console.warn("[Omni] listen omni-companion-mode failed:", err);
      });
    return () => {
      unlisten?.();
    };
  }, []);

  const requestHideChat = useCallback(async () => {
    void desktopDiagLog("react_hide_chat_request", { phase: "before_invoke" });
    try {
      // 乐观切陪伴 UI，与 Rust 立刻缩小窗口对齐；避免 mode=wait 时大窗已缩小仍渲主界面一帧
      setCompanionMode((prev) => (prev ? prev : true));
      const r = await invoke<HideChatWindowResult>("hide_chat_window");
      setCompanionMode(Boolean(r?.companion));
      void desktopDiagLog("react_hide_chat_ok", {
        companion: r?.companion,
        fullyHidden: r?.fullyHidden,
      });
    } catch (err) {
      console.error("[Omni] hide_chat_window failed:", err);
      void desktopDiagLog("react_hide_chat_err", { err: String(err) });
      setCompanionMode(false);
      // 陪伴坍缩失败时仍应能关掉窗口，避免 × / Esc 完全无响应
      try {
        const w = getCurrentWindow();
        await w.hide();
        setCompanionMode(false);
        void desktopDiagLog("react_hide_chat_fallback_hide_ok", {});
      } catch (e2) {
        console.error("[Omni] fallback hide() failed:", e2);
        void desktopDiagLog("react_hide_chat_fallback_hide_err", { err: String(e2) });
      }
    }
  }, []);

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

  // 唤醒词：弹出 Omni 条
  useEffect(() => {
    let unlisten: (() => void) | undefined;
    listen("WAKE_UP", () => {
      void invoke("show_chat_window");
    })
      .then((fn) => {
        unlisten = fn;
      })
      .catch(() => {});
    return () => {
      unlisten?.();
    };
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
        if (rid && mirrorRunIdRef.current && rid !== mirrorRunIdRef.current) return;
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
  const doActualSend = async (content: string, attachmentFiles: File[] = []) => {
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
    const attBuilt = await buildAttachmentsMetadataPayload(filesSnapshot);
    if (!attBuilt.ok) {
      setAttachmentHint(attBuilt.error);
      return;
    }
    if (filesSnapshot.length > 0 && !sensory.connected) {
      setAttachmentHint("发送附件需已连接 Layer 3（Sensory WebSocket，ws://localhost:18981）");
      return;
    }

    const turnSessionId = currentSessionIdRef.current;
    const namesLine =
      filesSnapshot.length > 0
        ? `📎 ${filesSnapshot.map((f) => f.name).join(", ")}`
        : "";
    const userBubbleText = [content.trim(), namesLine].filter(Boolean).join("\n\n");
    const userMessage: StoredMessage = { role: "user", content: userBubbleText, timestamp: Date.now() };
    const assistantMessage: StoredMessage = { role: "assistant", content: "", reasoning: "", timestamp: Date.now() };
    updateSessionMessagesById(turnSessionId, (m) => [...m, userMessage, assistantMessage]);
    setInput("");
    setPendingFiles([]);
    setAttachmentHint(null);
    setIsLoading(true);
    setRiskLevel("safe");
    setState("thinking");
    setIsTyping(true);
    l3ActiveRunIdRef.current = "";
    chatTurnTokenRef.current += 1;
    const myTurnToken = chatTurnTokenRef.current;
    l2StreamAbortRef.current?.abort();
    const l2Abort = new AbortController();
    l2StreamAbortRef.current = l2Abort;
    setLocalStreamChunkKind(null);

    const audioEl = chatAudioRef.current;
    const ttsQueue = ttsEnabled && audioEl ? createAudioQueue(audioEl, () => setState("idle")) : null;
    let accumulatedForTts = "";

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
    let reasoningPulseTimer: ReturnType<typeof setInterval> | null = null;
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
      if (myTurnToken !== chatTurnTokenRef.current) return;
      if (timeoutCleared) return;
      timeoutCleared = true;
      clearReasoningPulse();
      registerChunkHandler(null);
      clearTimeout(timeoutId);
      activeChatTurnTimeoutRef.current = null;
      updateSessionMessagesById(turnSessionId, (prev) => {
        const updated = [...prev];
        const last = updated[updated.length - 1];
        if (last?.role === "assistant") {
          if (opts?.skipContentUpdate) {
            // 流式已按 reasoning+content 拆开：收尾时对「全量串」再 normalize（含 Final Answer: 与标签）
            const combined = [last.reasoning ?? "", last.content ?? ""].filter(Boolean).join("\n\n");
            const n = normalizeAssistantOutput(
              combined.trim() ? combined : (finalContent || last.content || ""),
            );
            updated[updated.length - 1] = { ...last, content: n.content, reasoning: n.reasoning, source };
          } else {
            let newContent = finalContent || last.content;
            let newReasoning = last.reasoning ?? "";
            const n = normalizeAssistantOutput(newContent ?? "");
            newContent = n.content;
            newReasoning = [newReasoning, n.reasoning].filter(Boolean).join("\n\n").trim();
            updated[updated.length - 1] = { ...last, content: newContent, reasoning: newReasoning, source };
          }
        }
        return updated;
      });
      if (ttsQueue && finalContent) {
        const ttsSource = opts?.ttsUseFinalOnly ? finalContent : accumulatedForTts + finalContent;
        const { complete, remainder } = extractCompleteSentences(ttsSource);
        complete.forEach(enqueueSentence);
        if (remainder.trim()) enqueueSentence(remainder);
        ttsQueue.ensureIdle();
      }
      setIsLoading(false);
      setIsTyping(false);
      setLocalStreamChunkKind(null);
      setRiskLevel("safe");
      if (!ttsQueue) setTimeout(() => setState("idle"), 2000);
      registerAnswerHandler(null);
      registerStepHandler(null);
      const sv = opts?.sentryVariant ?? "answer";
      void maybeNotifyJachinAssistantDone(summarizeForSentryNotify(finalContent), sv);
    };

    const chunkHandler = (chunk: string, runId?: string, meta?: SensoryChunkMeta) => {
      if (myTurnToken !== chatTurnTokenRef.current) return;
      lastSensoryActivityAt = Date.now();
      if (runId) l3ActiveRunIdRef.current = runId;
      setLocalStreamChunkKind(meta?.isReasoning ? "reasoning" : "content");
      if (meta?.isReasoning) {
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
      accumulatedForTts += delta;
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

    const REASONING_PULSE_MS = 1000;
    const pulseHints = [
      "调度管线等待上游 token（chunk 流式）…",
      "Sensory WebSocket 已连接；若长时间无输出多为模型或工具阻塞。",
      "仍在合并流式片段，L3 run 进行中。",
      "可检查网络、API Key 或缩小单次请求范围。",
    ];
    let pulseIdx = 0;
    reasoningPulseTimer = window.setInterval(() => {
      if (myTurnToken !== chatTurnTokenRef.current) return;
      if (Date.now() - lastSensoryActivityAt < 900) return;
      updateSessionMessagesById(turnSessionId, (prev) => {
        const updated = [...prev];
        const last = updated[updated.length - 1];
        if (last?.role !== "assistant") return prev;
        const ts = new Date().toLocaleTimeString();
        const line = `\n[${ts}] [jachin:heartbeat] ${pulseHints[pulseIdx % pulseHints.length]}\n`;
        pulseIdx += 1;
        updated[updated.length - 1] = { ...last, reasoning: (last.reasoning ?? "") + line };
        return updated;
      });
    }, REASONING_PULSE_MS);

    const timeoutId = setTimeout(() => {
      if (myTurnToken !== chatTurnTokenRef.current) return;
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
      const rid = meta?.runId ?? "";
      if (rid && l3ActiveRunIdRef.current && rid !== l3ActiveRunIdRef.current) {
        console.debug("[Chat] 忽略陈旧 answer runId=%s 期望=%s", rid, l3ActiveRunIdRef.current);
        return;
      }
      clearReasoningPulse();
      registerChunkHandler(null);
      registerAnswerHandler(null);
      registerStepHandler(null);
      const isL3Error = typeof answerContent === "string" && (
        answerContent.includes("Ollama") || answerContent.includes("APIConnectionError") ||
        answerContent.includes("RuntimeError") || answerContent.includes("未配置 API Key")
      );
      if (isL3Error && pendingL3InputRef.current && l2Available) {
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
        // 与 Lark 一致：最终正文以服务端 answer 为准；流式仅作打字机，避免坏 chunk 永久留在气泡里
        const hadStream = meta?.hadStreamChunks ?? false;
        const useServerFinal = hadStream && typeof answerContent === "string" && answerContent.trim().length > 0;
        const sentryVariant: SentryNotifyVariant =
          meta?.terminalOutcome === "rejected"
            ? "rejected"
            : meta?.terminalOutcome === "error"
              ? "error"
              : "answer";
        cleanup(answerContent, "L3", {
          skipContentUpdate: !useServerFinal,
          ttsUseFinalOnly: useServerFinal,
          sentryVariant,
        });
      }
    });

    // 优先 L3（L3 直连大模型，自有 API Key），未连接时兜底 L2
    const attExtras =
      attBuilt.items.length > 0 ? { attachments_metadata: attBuilt.items } : undefined;
    if (sensory.connected) {
      const l3Ok = sendInput(intentForWire, attExtras);
      if (l3Ok) {
        console.debug("[Chat] L3 直连发送成功 sensory.connected=true");
        return; // L3 已接收，将通过 WebSocket 流式返回
      }
      console.debug("[Chat] L3 发送失败（可能 ws 未就绪），fallback L2");
    } else {
      console.debug("[Chat] L2 兜底 sensory.connected=false");
    }

    // L2 兜底前：若为 BI 等 L3 专用意图，优先尝试 L3 HTTP agent/run（Sensory WS 未连时也能触发）
    const l3Answer = await tryL3AgentForIntent(intentForWire);
    if (l3Answer != null && l3Answer.trim()) {
      console.debug("[Chat] L3 agent/run 命中 BI 意图，使用 L3 回复");
      clearTimeout(timeoutId);
      activeChatTurnTimeoutRef.current = null;
      cleanup(l3Answer, "L3");
      return;
    }

    // L2 兜底
    try {
      console.debug("[Chat] L2 streamChatMessage 开始");
      const fullText = await streamChatMessage(intentForWire, (chunk) => chunkHandler(chunk), {
        signal: l2Abort.signal,
      });
      if (myTurnToken !== chatTurnTokenRef.current) return;
      cleanup(fullText, "L2");
    } catch (e) {
      console.debug("[Chat] L2 streamChatMessage 失败:", (e as Error).message);
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

  /** 处理 /api/v1/voice/process 响应：IGNORE 仅提示已截取，ENGAGE 展示回复，REQUIRE_CONFIRMATION 红框 */
  const handleVoiceProcessResponse = (res: VoiceProcessResponse) => {
    if (res.intent_routing === "IGNORE") {
      setRecordingStatus("已截取一段（未触发回复）");
      setTimeout(() => setRecordingStatus(""), 2000);
      return;
    }
    if (res.security_action === "REQUIRE_CONFIRMATION" && res.reply_text) {
      setRiskLevel("danger");
      setPendingHighRisk({ text: res.recognized_text ?? "", strippedText: res.reply_text });
      return;
    }
    if (res.recognized_text) {
      setMessages((prev) => [...prev, { role: "user", content: `🎤 ${res.recognized_text}`, timestamp: Date.now() }]);
    }
    if (res.reply_text) {
      setMessages((prev) => [...prev, { role: "assistant", content: res.reply_text ?? "", timestamp: Date.now() }]);
    }
    if (res.reply_audio_base64 && chatAudioRef.current) {
      const bytes = Uint8Array.from(atob(res.reply_audio_base64), (c) => c.charCodeAt(0));
      const blob = new Blob([bytes], { type: "audio/wav" });
      const url = URL.createObjectURL(blob);
      chatAudioRef.current.src = url;
      setState("speaking");
      chatAudioRef.current.onended = () => { setState("idle"); URL.revokeObjectURL(url); };
      chatAudioRef.current.play().catch(() => setState("idle"));
    } else {
      setState("idle");
    }
  };

  useSttAudioReady({
    playOnReady: false,
    onReady: (payload) => {
      if (isRecordingRef.current || !isVadActiveRef.current || !payload?.wav_base64) return;
      setRecordingStatus("已截断一段，正在识别…");
      const ts = Math.floor(Date.now() / 1000);
      voiceProcess(payload.wav_base64, "vad", ts)
        .then((res) => {
          setRecordingStatus("");
          handleVoiceProcessResponse(res);
        })
        .catch(() => {
          setRecordingStatus("已截取一段，但后端语音处理暂未就绪");
          setTimeout(() => setRecordingStatus(""), 3000);
        });
    },
  });

  // 注意：清空/关闭等由 HolographicChat 或窗口控制

  // 开始录音（同时启动浏览器流式语音识别，实时显示转写）
  const startRecording = async () => {
    getCurrentWindow().setFocus().catch(() => {});
    setListeningText("");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      try {
        const audioCtx = new AudioContext();
        audioCtxRef.current = audioCtx;
        const source = audioCtx.createMediaStreamSource(stream);
        const analyser = audioCtx.createAnalyser();
        analyser.fftSize = 128;
        source.connect(analyser);
        const buf = new Uint8Array(analyser.frequencyBinCount);
        const loop = () => {
          analyser.getByteFrequencyData(buf);
          const v = buf.reduce((a, b) => a + b, 0) / buf.length / 255;
          setMicLevel(Math.min(1, v * 2.4));
          micRafRef.current = requestAnimationFrame(loop);
        };
        micRafRef.current = requestAnimationFrame(loop);
      } catch {
        /* 忽略 Analyser 失败，仍可用录音 */
      }

      const mediaRecorder = new MediaRecorder(stream);
      mediaRecorderRef.current = mediaRecorder;
      audioChunksRef.current = [];
      setIsRecording(true);
      setRecordingStatus("正在录音...");

      mediaRecorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          audioChunksRef.current.push(event.data);
        }
      };

      mediaRecorder.onstop = () => {
        stopMicAnalyser();
        const audioBlob = new Blob(audioChunksRef.current, { type: "audio/wav" });
        (window as any).recordedAudioBlob = audioBlob;
        stream.getTracks().forEach((track) => track.stop());
        if (speechRecognitionRef.current) {
          try {
            speechRecognitionRef.current.abort();
          } catch {}
          speechRecognitionRef.current = null;
        }
        setIsRecording(false);
        setRecordingStatus("录音完成");
        setListeningText("");
        handleVoiceChat(audioBlob);
      };

      mediaRecorder.start();

      const SpeechRecognitionCtor =
        typeof SpeechRecognition !== "undefined"
          ? SpeechRecognition
          : typeof webkitSpeechRecognition !== "undefined"
            ? webkitSpeechRecognition
            : null;
      if (SpeechRecognitionCtor) {
        const recognition = new SpeechRecognitionCtor();
        recognition.continuous = true;
        recognition.interimResults = true;
        recognition.lang = "zh-CN";
        recognition.onresult = (e: SpeechRecognitionEvent) => {
          let text = "";
          for (let i = 0; i < e.results.length; i++) {
            text += e.results[i][0].transcript;
          }
          if (text) setListeningText(text);
        };
        recognition.onerror = () => {};
        recognition.onend = () => {};
        speechRecognitionRef.current = recognition;
        recognition.start();
      }
      setState("listening");
    } catch (error: any) {
      stopMicAnalyser();
      setIsRecording(false);
      setListeningText("");
      setRecordingStatus(`无法访问麦克风: ${error.message}`);
      setState("idle");
    }
  };

  // 停止录音
  const stopRecording = () => {
    if (speechRecognitionRef.current) {
      try {
        speechRecognitionRef.current.abort();
      } catch {}
      speechRecognitionRef.current = null;
    }
    setListeningText("");
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== "inactive") {
      mediaRecorderRef.current.stop();
    }
  };

  // 语音聊天处理：优先走契约 /api/v1/voice/process (input_mode: manual)，失败则回退到旧 voiceChat
  const handleVoiceChat = async (audioBlob: Blob) => {
    setIsLoading(true);
    setRecordingStatus("正在处理语音...");
    setState("thinking");

    try {
      const ts = Math.floor(Date.now() / 1000);
      const wavBase64 = await blobToBase64(audioBlob);
      try {
        const res = await voiceProcess(wavBase64, "manual", ts);
        handleVoiceProcessResponse(res);
        setRecordingStatus("");
        return;
      } catch (_) {
        // 后端尚未实现 /api/v1/voice/process 时回退到旧接口
      }

      const audioFile = new File([audioBlob], "recording.wav", { type: "audio/wav" });
      const response = await voiceChat(audioFile, "wav", "zh-CN", true, "zh-CN-XiaoxiaoNeural");

      setMessages((prev) => [
        ...prev,
        { role: "user", content: `🎤 [语音] ${response.user_text || response.text || "已发送语音消息"}`, timestamp: Date.now() },
        { role: "assistant", content: "", reasoning: "", timestamp: Date.now() },
      ]);
      setIsTyping(true);
      let currentContent = "";
      await typewriterAnimation(response.text, {
        speed: 20,
        onUpdate: (text) => {
          currentContent = text;
          setMessages((prev) => {
            const updated = [...prev];
            updated[updated.length - 1] = { ...updated[updated.length - 1], content: currentContent };
            return updated;
          });
        },
        onComplete: () => {
          setIsTyping(false);
          setMessages((prev) => {
            const updated = [...prev];
            updated[updated.length - 1] = { ...updated[updated.length - 1], content: response.text };
            return updated;
          });
        },
      });
      if (response.audio_base64 && chatAudioRef.current) {
        const audioBytes = Uint8Array.from(atob(response.audio_base64), (c) => c.charCodeAt(0));
        const blob = new Blob([audioBytes], { type: "audio/wav" });
        const audioUrl = URL.createObjectURL(blob);
        chatAudioRef.current.src = audioUrl;
        setState("speaking");
        chatAudioRef.current.onended = () => { setState("idle"); URL.revokeObjectURL(audioUrl); };
        await chatAudioRef.current.play();
      } else {
        setState("idle");
      }
      setRecordingStatus("");
    } catch (error) {
      const msg = error instanceof Error ? error.message : "语音处理失败";
      const friendly = msg.toLowerCase().includes("fetch") || msg.toLowerCase().includes("network")
        ? "语音服务暂不可用（后端未启动，请运行 start.bat 或确保端口 18888 可达）"
        : msg;
      setMessages((prev) => [...prev, { role: "assistant", content: `错误: ${friendly}`, timestamp: Date.now() }]);
      setRecordingStatus(`错误: ${friendly}`);
      setState("idle");
    } finally {
      setIsLoading(false);
      setIsTyping(false);
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

  return (
    <div className="relative flex h-full w-full min-h-0 flex-col overflow-hidden bg-transparent">
      <AnimatePresence>
        {companionMode ? (
          <motion.div
            key="omni-spark"
            initial={{ opacity: 0, scale: 0.15 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.12 }}
            transition={{ duration: 0.24, ease: [0.22, 1, 0.36, 1] }}
            className="pointer-events-auto flex h-full min-h-0 w-full flex-col overflow-hidden"
          >
            <OmniMiniSpark onExpandFull={requestExpandFromSpark} />
          </motion.div>
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
                disabled={!sensory.connected && !l2Available}
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
        ref={chatAudioRef}
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
