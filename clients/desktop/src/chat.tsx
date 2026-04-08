/**
 * Chat / Omni 窗口 — Jachin Omni 极简输入条（无桌面精灵、无内嵌日志面板）
 *
 * 独立 chat 窗口入口；大控制台仍为 main（console.html）。
 * Sensory 步骤与回复逻辑与 `useSensoryWebSocket` + `sensoryStepFormat` 对齐（云端 v0.8.99 行为）。
 */

import React, { useState, useRef, useEffect, useMemo } from "react";
import ReactDOM from "react-dom/client";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { invoke } from "@tauri-apps/api/core";
import { voiceChat, synthesizeSpeech, voiceProcess, streamChatMessage, tryL3AgentForIntent, checkHealth, type VoiceProcessResponse } from "./lib/api";
import { useSpriteStore } from "./store/spriteStore";
import { useSttAudioReady } from "./hooks/useSttAudioReady";
import { useSensoryWebSocket, type SensoryAnswerMeta, type StreamChunkKind } from "./hooks/useSensoryWebSocket";
import { loadMessages, saveMessages, clearMessages, addMessage, StoredMessage } from "./utils/messageStorage";
import { extractCompleteSentences, createAudioQueue } from "./utils/streamingTts";
import { typewriterAnimation } from "./utils/typewriter";
import { CHAT_RESPONSE_TIMEOUT_MS, CHAT_RESPONSE_TIMEOUT_SEC } from "./constants/chatResponseTimeout";
import { mergeStreamChunk } from "./utils/streamChunkMerge";
import {
  createReasoningStreamAcc,
  mergeAssistantParts,
  normalizeAssistantOutput,
  processReasoningDelta,
  type ReasoningStreamAcc,
} from "./utils/reasoningStreamSplit";
import type { WavePhase } from "./components/Chat/VoiceWaveform";
import { OmniCyberChatShell, CorePhase } from "./components/Omni/JachinOmniCyberProtocol";
import { WindowResizeHandles } from "./components/Omni/WindowResizeHandles";
import { SensoryOverlay } from "./console/components/SensoryOverlay";
import { useJachinCoreState } from "./hooks/useJachinCoreState";
import "./styles/globals.css";

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

function ChatApp() {
  const [messages, setMessages] = useState<StoredMessage[]>([]);
  const [input, setInput] = useState("");
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
  const sensory = useSensoryWebSocket();
  const {
    handoffEvent,
    swarmEvent,
    streamChunkKind: wsStreamChunkKind,
    registerChunkHandler,
    registerAnswerHandler,
    registerStepHandler,
    registerMirrorInputHandler,
    sendInput,
    sendSessionClearControl,
    memoryCompactSuggest,
    sendMemoryCompactControl,
    dismissMemoryCompactSuggest,
  } = sensory;
  /** L2 等无 Sensory 累积串时，按 chunk 元数据同步 Core 相位 */
  const [localStreamChunkKind, setLocalStreamChunkKind] = useState<StreamChunkKind | null>(null);
  const streamChunkKindEffective = wsStreamChunkKind ?? localStreamChunkKind;
  const jachinCore = useJachinCoreState(sensory, { isTyping, localStreamChunkKind });
  /** 当前轮流式：<redacted_thinking> 与 metadata.is_reasoning 的解析状态 */
  const reasoningAccRef = useRef<ReasoningStreamAcc>(createReasoningStreamAcc());
  const mirrorReasoningAccRef = useRef<ReasoningStreamAcc>(createReasoningStreamAcc());
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

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        void invoke("hide_chat_window");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

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

  // 加载保存的消息历史
  useEffect(() => {
    const savedMessages = loadMessages();
    if (savedMessages.length > 0) {
      setMessages(savedMessages);
    }
  }, []);

  // 自动保存消息
  useEffect(() => {
    if (messages.length > 0) {
      saveMessages(messages);
    }
  }, [messages]);

  // 滚动由 HolographicChat 内部的 messagesEndRef 处理，此处不重复

  // Lark 镜像：Lark 用户发消息时，终端同步显示并接收后续回复
  useEffect(() => {
    const handler = (content: string) => {
      if (!content.trim() || isLoadingRef.current || isTypingRef.current) return;
      const displayContent = `[Lark] ${content.trim()}`;
      const userMsg: StoredMessage = { role: "user", content: displayContent, timestamp: Date.now() };
      setMessages((prev) => addMessage(prev, userMsg));
      const assistantMsg: StoredMessage = { role: "assistant", content: "", reasoning: "", timestamp: Date.now() };
      setMessages((prev) => addMessage(prev, assistantMsg));
      setIsLoading(true);
      setIsTyping(true);
      setLocalStreamChunkKind(null);
      mirrorReasoningAccRef.current = createReasoningStreamAcc();
      let mirrorStreamMerge = "";
      const mirrorRunIdRef = { current: "" };
      const chunkHandler = (chunk: string, runId?: string, meta?: { isReasoning?: boolean }) => {
        if (runId) mirrorRunIdRef.current = runId;
        setLocalStreamChunkKind(meta?.isReasoning ? "reasoning" : "content");
        if (meta?.isReasoning) {
          setMessages((prev) => {
            const u = [...prev];
            const last = u[u.length - 1];
            if (last?.role !== "assistant") return prev;
            const { content, reasoning } = processReasoningDelta(
              mirrorReasoningAccRef.current,
              last.content,
              last.reasoning ?? "",
              chunk,
              true,
            );
            const merged = mergeAssistantParts(content, reasoning);
            u[u.length - 1] = { ...last, content: merged.content, reasoning: merged.reasoning };
            return u;
          });
          return;
        }
        const { next, delta } = mergeStreamChunk(mirrorStreamMerge, chunk);
        mirrorStreamMerge = next;
        if (!delta) return;
        setMessages((prev) => {
          const u = [...prev];
          const last = u[u.length - 1];
          if (last?.role !== "assistant") return prev;
          const { content, reasoning } = processReasoningDelta(
            mirrorReasoningAccRef.current,
            last.content,
            last.reasoning ?? "",
            delta,
            false,
          );
          const merged = mergeAssistantParts(content, reasoning);
          u[u.length - 1] = { ...last, content: merged.content, reasoning: merged.reasoning };
          return u;
        });
      };
      const stepHandler = (_step: string, stepContent: string, runId?: string) => {
        if (runId) mirrorRunIdRef.current = runId;
        setMessages((prev) => {
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
        setMessages((prev) => {
          const u = [...prev];
          const last = u[u.length - 1];
          if (last?.role === "assistant") {
            let newContent = useServerFinal
              ? answerContent
              : !hadStream
                ? answerContent || last.content
                : last.content;
            let newReasoning = last.reasoning ?? "";
            const n = normalizeAssistantOutput(newContent);
            newContent = n.content;
            newReasoning = [newReasoning, n.reasoning].filter(Boolean).join("\n\n").trim();
            u[u.length - 1] = {
              ...last,
              content: newContent,
              reasoning: newReasoning,
              source: "L3",
            };
          }
          return u;
        });
        setIsLoading(false);
        setIsTyping(false);
        setLocalStreamChunkKind(null);
        registerChunkHandler(null);
        registerAnswerHandler(null);
        registerStepHandler(null);
      };
      registerChunkHandler(chunkHandler);
      registerStepHandler(stepHandler);
      registerAnswerHandler(answerHandler);
    };
    registerMirrorInputHandler(handler);
    return () => registerMirrorInputHandler(null);
  }, [registerMirrorInputHandler, registerChunkHandler, registerAnswerHandler, registerStepHandler]);


  /** 实际发送消息：优先 L3 Sensory，未连接时直连 L2 文本 API（与语音同源） */
  const doActualSend = async (content: string) => {
    if (content.trim() === "/clear") {
      registerChunkHandler(null);
      registerAnswerHandler(null);
      registerStepHandler(null);
      clearMessages();
      const systemLine: StoredMessage = {
        role: "system",
        content: "🧹 统帅，当前会话上下文已物理清空，大模型已进入失忆状态。",
        timestamp: Date.now(),
      };
      setMessages([systemLine]);
      saveMessages([systemLine]);
      setInput("");
      setIsLoading(false);
      setIsTyping(false);
      setRiskLevel("safe");
      setState("idle");
      setLocalStreamChunkKind(null);
      l3ActiveRunIdRef.current = "";
      sendSessionClearControl();
      return;
    }

    const userMessage: StoredMessage = { role: "user", content, timestamp: Date.now() };
    setMessages((prev) => addMessage(prev, userMessage));
    setInput("");
    setIsLoading(true);
    setRiskLevel("safe");
    setState("thinking");

    const assistantMessage: StoredMessage = { role: "assistant", content: "", reasoning: "", timestamp: Date.now() };
    setMessages((prev) => addMessage(prev, assistantMessage));
    setIsTyping(true);
    l3ActiveRunIdRef.current = "";
    setLocalStreamChunkKind(null);
    reasoningAccRef.current = createReasoningStreamAcc();

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
    const cleanup = (
      finalContent: string,
      source?: "L3" | "L2",
      opts?: { skipContentUpdate?: boolean; ttsUseFinalOnly?: boolean },
    ) => {
      if (timeoutCleared) return;
      timeoutCleared = true;
      registerChunkHandler(null);
      clearTimeout(timeoutId);
      setMessages((prev) => {
        const updated = [...prev];
        const last = updated[updated.length - 1];
        if (last?.role === "assistant") {
          let newContent = last.content;
          if (opts?.skipContentUpdate) {
            // 已有 chunk 流式内容则保留，避免与最终 answer 重复；若仍为空则用 answer 填满（仅 answer、无 chunk 时否则会空白）
            newContent = last.content?.trim() ? last.content : (finalContent || last.content);
          } else {
            newContent = finalContent || last.content;
          }
          let newReasoning = last.reasoning ?? "";
          const n = normalizeAssistantOutput(newContent);
          newContent = n.content;
          newReasoning = [newReasoning, n.reasoning].filter(Boolean).join("\n\n").trim();
          updated[updated.length - 1] = { ...last, content: newContent, reasoning: newReasoning, source };
        }
        saveMessages(updated);
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
    };

    const chunkHandler = (chunk: string, runId?: string, meta?: { isReasoning?: boolean }) => {
      if (runId) l3ActiveRunIdRef.current = runId;
      setLocalStreamChunkKind(meta?.isReasoning ? "reasoning" : "content");
      if (meta?.isReasoning) {
        setMessages((prev) => {
          const updated = [...prev];
          const last = updated[updated.length - 1];
          if (last?.role !== "assistant") return prev;
          const { content, reasoning } = processReasoningDelta(
            reasoningAccRef.current,
            last.content,
            last.reasoning ?? "",
            chunk,
            true,
          );
          const merged = mergeAssistantParts(content, reasoning);
          updated[updated.length - 1] = { ...last, content: merged.content, reasoning: merged.reasoning };
          return updated;
        });
        return;
      }
      const { next, delta } = mergeStreamChunk(streamMergeAcc, chunk);
      streamMergeAcc = next;
      if (!delta) return;
      accumulatedForTts += delta;
      setMessages((prev) => {
        const updated = [...prev];
        const last = updated[updated.length - 1];
        if (last?.role === "assistant") {
          const { content, reasoning } = processReasoningDelta(
            reasoningAccRef.current,
            last.content,
            last.reasoning ?? "",
            delta,
            false,
          );
          const merged = mergeAssistantParts(content, reasoning);
          updated[updated.length - 1] = { ...last, content: merged.content, reasoning: merged.reasoning };
        }
        return updated;
      });
    };

    const stepHandler = (stepType: string, content: string, runId?: string) => {
      if (runId) l3ActiveRunIdRef.current = runId;
      setMessages((prev) => {
        const updated = [...prev];
        const last = updated[updated.length - 1];
        if (last?.role === "assistant") {
          const reasoning = (last.reasoning ?? "") + content;
          updated[updated.length - 1] = { ...last, reasoning };
        }
        return updated;
      });
    };

    const timeoutId = setTimeout(() => {
      registerChunkHandler(null);
      registerStepHandler(null);
      // 保留 answer：L3 可能在 compaction 后晚于本定时器返回，与 Lark 同源的最终包仍应写入气泡
      setMessages((prev) => {
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

    const pendingL3InputRef = { current: content };
    registerChunkHandler(chunkHandler);
    registerStepHandler(stepHandler);
    registerAnswerHandler((answerContent, meta) => {
      const rid = meta?.runId ?? "";
      if (rid && l3ActiveRunIdRef.current && rid !== l3ActiveRunIdRef.current) {
        console.debug("[Chat] 忽略陈旧 answer runId=%s 期望=%s", rid, l3ActiveRunIdRef.current);
        return;
      }
      registerChunkHandler(null);
      registerAnswerHandler(null);
      registerStepHandler(null);
      const isL3Error = typeof answerContent === "string" && (
        answerContent.includes("Ollama") || answerContent.includes("APIConnectionError") ||
        answerContent.includes("RuntimeError") || answerContent.includes("未配置 API Key")
      );
      if (isL3Error && pendingL3InputRef.current && l2Available) {
        console.debug("[Chat] L3 返回错误，兜底 L2:", answerContent.slice(0, 80));
        streamChatMessage(pendingL3InputRef.current, (chunk) => chunkHandler(chunk))
          .then((fullText) => {
            registerAnswerHandler(null);
            registerStepHandler(null);
            cleanup(fullText, "L2");
          })
          .catch((e) => {
            registerAnswerHandler(null);
            registerStepHandler(null);
            cleanup(`L2 兜底也失败：${(e as Error).message}`, "L2");
          });
      } else {
        // 与 Lark 一致：最终正文以服务端 answer 为准；流式仅作打字机，避免坏 chunk 永久留在气泡里
        const hadStream = meta?.hadStreamChunks ?? false;
        const useServerFinal = hadStream && typeof answerContent === "string" && answerContent.trim().length > 0;
        cleanup(answerContent, "L3", {
          skipContentUpdate: !useServerFinal,
          ttsUseFinalOnly: useServerFinal,
        });
      }
    });

    // 优先 L3（L3 直连大模型，自有 API Key），未连接时兜底 L2
    if (sensory.connected && sendInput(content)) {
      console.debug("[Chat] L3 直连发送成功 sensory.connected=true");
      return; // L3 已接收，将通过 WebSocket 流式返回
    }
    if (sensory.connected && !sendInput(content)) {
      console.debug("[Chat] L3 发送失败（可能 ws 未就绪），fallback L2");
    } else if (!sensory.connected) {
      console.debug("[Chat] L2 兜底 sensory.connected=false");
    }

    // L2 兜底前：若为 BI 等 L3 专用意图，优先尝试 L3 HTTP agent/run（Sensory WS 未连时也能触发）
    const l3Answer = await tryL3AgentForIntent(content);
    if (l3Answer != null && l3Answer.trim()) {
      console.debug("[Chat] L3 agent/run 命中 BI 意图，使用 L3 回复");
      clearTimeout(timeoutId);
      cleanup(l3Answer, "L3");
      return;
    }

    // L2 兜底
    try {
      console.debug("[Chat] L2 streamChatMessage 开始");
      const fullText = await streamChatMessage(content, (chunk) => chunkHandler(chunk));
      cleanup(fullText, "L2");
    } catch (e) {
      console.debug("[Chat] L2 streamChatMessage 失败:", (e as Error).message);
      clearTimeout(timeoutId);
      registerAnswerHandler(null);
      registerStepHandler(null);
      registerChunkHandler(null);
      setMessages((prev) => {
        const updated = [...prev];
        const last = updated[updated.length - 1];
        if (last?.role === "assistant" && !last.content?.trim()) {
          updated[updated.length - 1] = { ...last, content: `打字请求失败：${(e as Error).message}。请确认 Layer 3 (ws://localhost:18981) 或 Layer 2 (http://localhost:18888) 已启动。` };
        }
        return updated;
      });
      setIsLoading(false);
      setIsTyping(false);
    }
  };

  const handleSend = async () => {
    if (!input.trim() || isLoading || isTyping) return;
    await doActualSend(input.trim());
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
      setMessages((prev) => addMessage(prev, { role: "user", content: `🎤 ${res.recognized_text}`, timestamp: Date.now() }));
    }
    if (res.reply_text) {
      setMessages((prev) => addMessage(prev, { role: "assistant", content: res.reply_text ?? "", timestamp: Date.now() }));
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

      setMessages((prev) => addMessage(prev, { role: "user", content: `🎤 [语音] ${response.user_text || response.text || "已发送语音消息"}`, timestamp: Date.now() }));
      setMessages((prev) => addMessage(prev, { role: "assistant", content: "", reasoning: "", timestamp: Date.now() }));
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
            saveMessages(updated);
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
      setMessages((prev) => addMessage(prev, { role: "assistant", content: `错误: ${friendly}`, timestamp: Date.now() }));
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
    void invoke("hide_chat_window");
  };

  const lastAssistantBubble = [...messages].reverse().find((m) => m.role === "assistant");
  /** 当前轮助手可见文本（正文 + 思考），用于 Core 流式相位判断 */
  const streamDisplay =
    (lastAssistantBubble?.content ?? "") + (lastAssistantBubble?.reasoning ?? "");

  /** 与 useJachinCoreState 对齐，映射到赛博协议 CorePhase（驱动 JachinCore 流光） */
  const cyberPhase = useMemo((): CorePhase => {
    if (jachinCore.selfHealFlash) return CorePhase.HEALING;
    if (sensory.hitlPending) return CorePhase.THINKING;
    // reasoning 流式：狂暴 THINKING（须先于「有字即 STREAMING」）
    if (
      isTyping &&
      streamChunkKindEffective === "reasoning" &&
      streamDisplay.trim().length > 0
    ) {
      return CorePhase.THINKING;
    }
    if (isTyping && streamDisplay.trim().length > 0) return CorePhase.STREAMING;
    if (isLoading && !isRecording && !isVadActive) return CorePhase.THINKING;
    if (jachinCore.coreState === "thinking") return CorePhase.THINKING;
    if (jachinCore.coreState === "streaming" && streamDisplay.trim().length > 0) {
      return CorePhase.STREAMING;
    }
    return CorePhase.IDLE;
  }, [
    jachinCore.selfHealFlash,
    jachinCore.coreState,
    isTyping,
    streamDisplay,
    streamChunkKindEffective,
    sensory.hitlPending,
    isLoading,
    isRecording,
    isVadActive,
  ]);

  return (
    <div className="relative flex h-full w-full min-h-0 flex-col overflow-hidden bg-transparent">
      <WindowResizeHandles />
      {/* v8.0 全息感官：Handoff + Swarm + HITL 等 */}
      <SensoryOverlay sensory={sensory} variant="minimal" />
      <div className="pointer-events-none flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        <div className="flex h-full min-h-0 flex-1 flex-col overflow-hidden pointer-events-auto">
          {memoryCompactSuggest && (
            <div className="z-30 mx-2 mt-2 shrink-0 rounded-lg border border-amber-500/45 bg-amber-950/95 px-3 py-2.5 text-xs text-amber-100 shadow-lg backdrop-blur-sm">
              <p className="mb-1 font-medium text-amber-50">记忆整理提醒</p>
              <p className="mb-2 leading-relaxed text-amber-100/90">{memoryCompactSuggest.content}</p>
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-amber-200/85">
                  {memoryCompactSuggest.remainingSec > 0
                    ? `${memoryCompactSuggest.remainingSec} 秒后自动开始整理（后台）…`
                    : "正在请求启动…"}
                </span>
                <button
                  type="button"
                  className="rounded-md bg-amber-500/90 px-2.5 py-1 text-[11px] font-medium text-amber-950 hover:bg-amber-400"
                  onClick={() => sendMemoryCompactControl("memory_compact_confirm")}
                >
                  立即开始
                </button>
                <button
                  type="button"
                  className="rounded-md bg-white/10 px-2.5 py-1 text-[11px] text-amber-100 hover:bg-white/15"
                  onClick={() => sendMemoryCompactControl("memory_compact_defer", 24)}
                >
                  推迟 24 小时
                </button>
                <button
                  type="button"
                  className="rounded-md px-2 py-1 text-[11px] text-amber-200/70 hover:text-amber-100"
                  onClick={() => dismissMemoryCompactSuggest()}
                >
                  关闭提示
                </button>
              </div>
            </div>
          )}
          <OmniCyberChatShell
            phase={cyberPhase}
            thinkingToolFlash={jachinCore.toolFlash}
            messages={messages}
            input={input}
            onInputChange={setInput}
            onSend={handleSend}
            placeholder={
              sensory.connected
                ? "Alt+Shift+Space · 输入或按住说话（L3）…"
                : l2Available
                  ? "Alt+Shift+Space · 输入指令（L2）…"
                  : "等待 L3 或 L2…"
            }
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
          />
        </div>
      </div>
      {/* 高风险操作二次确认弹窗 */}
      {pendingHighRisk && (
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
