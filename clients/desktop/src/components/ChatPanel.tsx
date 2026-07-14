import { useState, useRef, useEffect, useLayoutEffect, useCallback, useMemo } from "react";
import { Send, Loader2, Mic, Square, Trash2, MicOff } from "lucide-react";
import { invoke } from "@tauri-apps/api/core";
import { voiceChat, streamChatMessage } from "../lib/api";
import { DEFAULT_KOKORO_TTS_VOICE } from "../voice/voiceDefaults";
import { useAppStore } from "../store/appStore";
import { useSttAudioReady } from "../hooks/useSttAudioReady";
import { useSensoryWebSocket, type SensoryAnswerMeta } from "../hooks/useSensoryWebSocket";
import { cn } from "../utils/cn";
import {
  loadMessages,
  saveMessages,
  clearMessages,
  addMessage,
  findUnresolvedToolCallMessageIndex,
  dismissUnresolvedToolCallMessage,
  StoredMessage,
} from "../utils/messageStorage";
import { normalizeAssistantOutput } from "../utils/reasoningStreamSplit";
import type { ToolUiSubmitPayload } from "../skills-ui/types";
import { getActiveSkillCanvasFromMessages, SkillCanvasPane } from "../skills-ui";
import { expandChatWindowForSkillCanvas, SKILL_CHAT_COLUMN_WIDTH } from "../skills-ui/skillCanvasWindow";
import { typewriterAnimation } from "../utils/typewriter";
import { AssistantMessageContent } from "./Chat/AssistantMessageContent";
import { CHAT_RESPONSE_TIMEOUT_MS, CHAT_RESPONSE_TIMEOUT_SEC } from "../constants/chatResponseTimeout";

export default function ChatPanel() {
  const [messages, setMessages] = useState<StoredMessage[]>([]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [isRecording, setIsRecording] = useState(false);
  const [recordingStatus, setRecordingStatus] = useState<string>("");
  const [isTyping, setIsTyping] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const chatAudioRef = useRef<HTMLAudioElement | null>(null);
  const l3PanelRunIdRef = useRef<string>("");
  const { isConnected } = useAppStore();
  const sensory = useSensoryWebSocket();
  const {
    connected: sensoryConnected,
    sendInput,
    sendToolUiResult,
    sendSessionClearControl,
    registerChunkHandler,
    registerAnswerHandler,
    registerStepHandler,
    registerMirrorInputHandler,
    registerBackgroundTaskHandler,
    zombieTasksPending,
    dismissZombieTasksPending,
    backgroundTaskPulse,
  } = sensory;
  const [isVoiceCaptureRunning, setIsVoiceCaptureRunning] = useState(false);

  // VAD 截断事件：收到后自动播放以验证截断效果
  useSttAudioReady({ playOnReady: true });

  // 加载保存的消息历史
  useEffect(() => {
    const savedMessages = loadMessages();
    if (savedMessages.length > 0) {
      setMessages(savedMessages);
    }
  }, []);

  useEffect(() => {
    registerBackgroundTaskHandler((ev) => {
      if (ev.event === "zombie_tasks_pending") {
        return;
      }
      if (ev.event !== "completed" && ev.event !== "failed" && ev.event !== "cancelled") {
        return;
      }
      const taskId = ev.task_id ?? "";
      let text = "";
      if (ev.event === "completed") {
        const preview = (ev.result_preview || "").trim();
        text =
          `### 后台任务已完成\n\n` +
          `- **任务 ID：** \`${taskId}\`\n` +
          (preview ? `\n**结果摘要：**\n\n${preview}\n` : "\n") +
          `\n如需完整输出，可在对话中说明「查询该任务结果」或请助手调用 \`core:check_background_task\`（传入该 task_id）。`;
      } else if (ev.event === "failed") {
        text =
          `### 后台任务失败\n\n- **任务 ID：** \`${taskId}\`` +
          (ev.message ? `\n\n**原因：** ${ev.message}` : "");
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
      setMessages((prev) => addMessage(prev, msg));
    });
    return () => registerBackgroundTaskHandler(null);
  }, [registerBackgroundTaskHandler]);

  // 自动保存消息
  useEffect(() => {
    if (messages.length > 0) {
      saveMessages(messages);
    }
  }, [messages]);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  /** 生成式 UI：经 L3 `tool_ui_result` 执行工具，answer 写回气泡（与 chat.tsx 一致） */
  const handleToolUiResult = useCallback(
    async (payload: ToolUiSubmitPayload): Promise<void> => {
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
          setMessages((prev) => {
            const idx = findUnresolvedToolCallMessageIndex(prev, payload);
            if (idx < 0) return prev;
            const next = [...prev];
            const cur = next[idx];
            next[idx] = {
              ...cur,
              tool_call: { ...cur.tool_call!, resolved: true },
              content: "提交超时：请确认 L3 WebSocket 已连接。",
              source: "L3",
            };
            saveMessages(next);
            return next;
          });
          finish();
        }, CHAT_RESPONSE_TIMEOUT_MS);

        registerAnswerHandler((answerContent) => {
          window.clearTimeout(timer);
          registerAnswerHandler(null);
          setMessages((prev) => {
            const idx = findUnresolvedToolCallMessageIndex(prev, payload);
            if (idx < 0) return prev;
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
            saveMessages(next);
            return next;
          });
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
          setMessages((prev) => {
            const idx = findUnresolvedToolCallMessageIndex(prev, payload);
            if (idx < 0) return prev;
            const next = [...prev];
            const cur = next[idx];
            next[idx] = {
              ...cur,
              tool_call: { ...cur.tool_call!, resolved: true },
              content: "无法连接 L3 WebSocket。",
              source: "L3",
            };
            saveMessages(next);
            return next;
          });
          finish();
        }
      });
    },
    [registerAnswerHandler, registerStepHandler, sendToolUiResult],
  );

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  // Lark 镜像：终端同步显示；answer 与 Lark 推送同源，有 chunk 时用最终 answer 覆盖气泡
  useEffect(() => {
    const handler = (content: string) => {
      if (!content.trim() || isLoading || isTyping) return;
      const displayContent = `[Lark] ${content.trim()}`;
      const userMsg: StoredMessage = { role: "user", content: displayContent, timestamp: Date.now() };
      setMessages((prev) => addMessage(prev, userMsg));
      const assistantMsg: StoredMessage = { role: "assistant", content: "", timestamp: Date.now() };
      setMessages((prev) => addMessage(prev, assistantMsg));
      setIsLoading(true);
      setIsTyping(true);
      const mirrorRunIdRef = { current: "" };
      const chunkHandler = (chunk: string, runId?: string) => {
        if (runId) mirrorRunIdRef.current = runId;
        setMessages((prev) => {
          const u = [...prev];
          const last = u[u.length - 1];
          if (last?.role === "assistant") u[u.length - 1] = { ...last, content: last.content + chunk };
          return u;
        });
      };
      const stepHandler = (_step: string, stepContent: string, runId?: string) => {
        if (runId) mirrorRunIdRef.current = runId;
        setMessages((prev) => {
          const u = [...prev];
          const last = u[u.length - 1];
          if (last?.role === "assistant") u[u.length - 1] = { ...last, content: last.content + stepContent };
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
            const next = {
              ...last,
              content: useServerFinal
                ? answerContent
                : !hadStream
                  ? answerContent || last.content
                  : last.content,
              source: "L3" as const,
            };
            u[u.length - 1] = next;
          }
          saveMessages(u);
          return u;
        });
        setIsLoading(false);
        setIsTyping(false);
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
  }, [isLoading, isTyping, registerChunkHandler, registerAnswerHandler, registerStepHandler, registerMirrorInputHandler]);

  const handleSend = async (overrideText?: string) => {
    const pendingInput = (overrideText ?? input).trim();
    if (!pendingInput || isLoading || isTyping) return;

    if (pendingInput === "/clear") {
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
      sendSessionClearControl();
      return;
    }

    const content = pendingInput;
    const userMessage: StoredMessage = {
      role: "user",
      content,
      timestamp: Date.now(),
    };
    setMessages((prev) => addMessage(prev, userMessage));
    setInput("");
    setIsLoading(true);

    const assistantMessage: StoredMessage = {
      role: "assistant",
      content: "",
      timestamp: Date.now(),
    };
    setMessages((prev) => addMessage(prev, assistantMessage));
    setIsTyping(true);
    l3PanelRunIdRef.current = "";

    const timeoutId = setTimeout(() => {
      registerChunkHandler(null);
      registerStepHandler(null);
      setMessages((prev) => {
        const last = prev[prev.length - 1];
        if (last?.role === "assistant" && !last.content) {
          return [
            ...prev.slice(0, -1),
            {
              ...last,
              content: `响应超时（${CHAT_RESPONSE_TIMEOUT_SEC} 秒）；长任务可在 .env 设置 VITE_CHAT_RESPONSE_TIMEOUT_MS`,
            },
          ];
        }
        return prev;
      });
      setIsLoading(false);
      setIsTyping(false);
    }, CHAT_RESPONSE_TIMEOUT_MS);

    const cleanup = (finalContent: string, source?: "L3" | "L2", opts?: { skipContentUpdate?: boolean }) => {
      clearTimeout(timeoutId);
      registerChunkHandler(null);
      registerAnswerHandler(null);
      registerStepHandler(null);
      setMessages((prev) => {
        const updated = [...prev];
        const last = updated[updated.length - 1];
        if (last?.role === "assistant") {
          if (!opts?.skipContentUpdate) {
            updated[updated.length - 1] = { ...last, content: finalContent || last.content, source };
          } else {
            updated[updated.length - 1] = { ...last, source };
          }
        }
        saveMessages(updated);
        return updated;
      });
      setIsLoading(false);
      setIsTyping(false);
    };

    const chunkHandler = (chunk: string, runId?: string) => {
      if (runId) l3PanelRunIdRef.current = runId;
      setMessages((prev) => {
        const updated = [...prev];
        const last = updated[updated.length - 1];
        if (last?.role === "assistant") {
          updated[updated.length - 1] = { ...last, content: last.content + chunk };
        }
        return updated;
      });
    };

    const stepHandler = (_stepType: string, stepContent: string, runId?: string) => {
      if (runId) l3PanelRunIdRef.current = runId;
      setMessages((prev) => {
        const updated = [...prev];
        const last = updated[updated.length - 1];
        if (last?.role === "assistant") {
          updated[updated.length - 1] = { ...last, content: last.content + stepContent };
        }
        return updated;
      });
    };

    registerChunkHandler(chunkHandler);
    registerStepHandler(stepHandler);
    registerAnswerHandler((answerContent, meta) => {
      const rid = meta?.runId ?? "";
      if (rid && l3PanelRunIdRef.current && rid !== l3PanelRunIdRef.current) return;
      const hadStream = meta?.hadStreamChunks ?? false;
      const useServerFinal = hadStream && !!(answerContent || "").trim();
      cleanup(answerContent, "L3", { skipContentUpdate: !useServerFinal });
    });

    // 优先 L3（L3 直连大模型），未连接时兜底 L2
    if (sensoryConnected && sendInput(content)) {
      return; // L3 已接收，将通过 WebSocket 流式返回
    }

    // L2 兜底
    try {
      const fullText = await streamChatMessage(content, chunkHandler);
      registerChunkHandler(null);
      registerAnswerHandler(null);
      registerStepHandler(null);
      cleanup(fullText, "L2");
    } catch (e) {
      clearTimeout(timeoutId);
      registerChunkHandler(null);
      registerAnswerHandler(null);
      registerStepHandler(null);
      setMessages((prev) => {
        const updated = [...prev];
        const last = updated[updated.length - 1];
        if (last?.role === "assistant" && !last.content) {
          updated[updated.length - 1] = { ...last, content: `打字失败：${(e as Error).message}。请确认 Layer 3 (ws://localhost:18981) 或 Layer 2 (http://localhost:18888) 已启动。` };
        }
        return updated;
      });
      setIsLoading(false);
      setIsTyping(false);
    }
  };

  const handleClearMessages = () => {
    if (window.confirm("确定要清空所有消息吗？")) {
      clearMessages();
      setMessages([]);
    }
  };

  const handleKeyPress = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  // 开始录音（语音走 L2，需 L2 连接）
  const startRecording = async () => {
    if (!isConnected) {
      setRecordingStatus("请先连接 L2 服务（语音需 L2）");
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
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
        const audioBlob = new Blob(audioChunksRef.current, { type: 'audio/wav' });
        stream.getTracks().forEach(track => track.stop());
        setIsRecording(false);
        setRecordingStatus("录音完成，正在处理...");

        // 自动发送语音消息
        handleVoiceChat(audioBlob);
      };

      mediaRecorder.start();
    } catch (error: any) {
      setIsRecording(false);
      setRecordingStatus(`无法访问麦克风: ${error.message}`);
    }
  };

  // 停止录音
  const stopRecording = () => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      mediaRecorderRef.current.stop();
    }
  };

  // 语音聊天处理
  const handleVoiceChat = async (audioBlob: Blob) => {
    setIsLoading(true);

    try {
      // 将 Blob 转换为 File
      const audioFile = new File([audioBlob], 'recording.wav', { type: 'audio/wav' });

      // 调用语音聊天API
      const response = await voiceChat(audioFile, 'wav', 'zh-CN', true, DEFAULT_KOKORO_TTS_VOICE);

      // 添加用户消息（显示识别出的文本）
      const userMessage: StoredMessage = {
        role: "user",
        content: `🎤 [语音] ${response.user_text || response.text || '已发送语音消息'}`,
        timestamp: Date.now(),
      };
      setMessages((prev) => addMessage(prev, userMessage));

      // 添加AI回复（使用打字机效果）
      const assistantMessage: StoredMessage = {
        role: "assistant",
        content: "",
        timestamp: Date.now(),
      };
      setMessages((prev) => addMessage(prev, assistantMessage));

      // 使用打字机效果显示回复
      setIsTyping(true);
      let currentContent = "";

      await typewriterAnimation(response.text, {
        speed: 20,
        onUpdate: (text) => {
          currentContent = text;
          setMessages((prev) => {
            const updated = [...prev];
            updated[updated.length - 1] = {
              ...updated[updated.length - 1],
              content: currentContent,
            };
            return updated;
          });
        },
        onComplete: () => {
          setIsTyping(false);
          setMessages((prev) => {
            const updated = [...prev];
            updated[updated.length - 1] = {
              ...updated[updated.length - 1],
              content: response.text,
            };
            saveMessages(updated);
            return updated;
          });
        },
      });

      // 播放语音回复
      if (response.audio_base64 && chatAudioRef.current) {
        const audioBytes = Uint8Array.from(atob(response.audio_base64), c => c.charCodeAt(0));
        const audioBlob = new Blob([audioBytes], { type: 'audio/wav' });
        const audioUrl = URL.createObjectURL(audioBlob);

        chatAudioRef.current.src = audioUrl;
        await chatAudioRef.current.play();
      }

      setRecordingStatus("");
    } catch (error) {
      const errorMessage: StoredMessage = {
        role: "assistant",
        content: `错误: ${error instanceof Error ? error.message : "语音处理失败"}`,
        timestamp: Date.now(),
      };
      setMessages((prev) => addMessage(prev, errorMessage));
      setRecordingStatus(`错误: ${error instanceof Error ? error.message : "未知错误"}`);
    } finally {
      setIsLoading(false);
      setIsTyping(false);
    }
  };

  // VAD 语音采集：开始/停止
  const startVoiceCapture = async () => {
    try {
      await invoke("start_voice_capture");
      setIsVoiceCaptureRunning(true);
    } catch (e) {
      setRecordingStatus(String(e));
    }
  };
  const stopVoiceCapture = async () => {
    try {
      await invoke("stop_voice_capture");
      setIsVoiceCaptureRunning(false);
    } catch (e) {
      setRecordingStatus(String(e));
    }
  };
  useEffect(() => {
    let cancelled = false;
    const check = async () => {
      try {
        const running = await invoke<boolean>("is_voice_capture_running");
        if (!cancelled) setIsVoiceCaptureRunning(running);
      } catch {
        if (!cancelled) setIsVoiceCaptureRunning(false);
      }
    };
    check();
    return () => {
      cancelled = true;
    };
  }, []);

  // 自动调整输入框高度
  useEffect(() => {
    const textarea = document.querySelector("textarea");
    if (textarea) {
      textarea.style.height = "auto";
      textarea.style.height = `${Math.min(textarea.scrollHeight, 120)}px`;
    }
  }, [input]);

  const activeSkillCanvas = useMemo(() => getActiveSkillCanvasFromMessages(messages), [messages]);

  useLayoutEffect(() => {
    if (!activeSkillCanvas) return;
    let cancelled = false;
    void (async () => {
      await expandChatWindowForSkillCanvas();
      if (cancelled) return;
      await new Promise<void>((r) => requestAnimationFrame(() => r()));
      if (cancelled) return;
      await expandChatWindowForSkillCanvas();
      if (cancelled) return;
      await new Promise<void>((r) => requestAnimationFrame(() => r()));
      if (cancelled) return;
      await expandChatWindowForSkillCanvas();
    })();
    return () => {
      cancelled = true;
    };
  }, [activeSkillCanvas]);

  const handleDismissSkillCanvas = useCallback(() => {
    setMessages((prev) => {
      const active = getActiveSkillCanvasFromMessages(prev);
      if (!active) return prev;
      const next = dismissUnresolvedToolCallMessage(prev, active);
      saveMessages(next);
      return next;
    });
  }, []);

  return (
    <div className="h-full flex flex-col bg-slate-800/50 rounded-lg border border-purple-500/20 overflow-hidden">
      <div className="flex h-full min-h-0 w-full flex-1 flex-row items-stretch overflow-hidden">
        <div
          className={`flex min-h-0 flex-col overflow-hidden ${activeSkillCanvas ? "shrink-0" : "min-w-0 flex-1"}`}
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
      {/* 消息区域 */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {messages.length === 0 ? (
          <div className="h-full flex items-center justify-center text-slate-400">
            <div className="text-center">
              <div className="text-4xl mb-4">🤖</div>
              <p>开始与 Jachin AI 助手对话</p>
              <p className="text-sm mt-2 text-slate-500">
                {sensoryConnected
                  ? "已连接 L3（直连大模型）"
                  : isConnected
                    ? "已连接 L2（兜底）"
                    : "等待 L3 或 L2 连接..."}
              </p>
            </div>
          </div>
        ) : (
          <>
            {messages.map((msg, idx) => (
              <div
                key={idx}
                className={`flex ${
                  msg.role === "user" ? "justify-end" : "justify-start"
                }`}
              >
                <div
                  className={cn(
                    "max-w-[80%] rounded-lg px-4 py-2",
                    msg.role === "user"
                      ? "bg-purple-600 text-white"
                      : "bg-slate-700 text-slate-100"
                  )}
                >
                  <div className="whitespace-pre-wrap">
                    {msg.role === "assistant" ? (
                      <AssistantMessageContent
                        message={msg}
                        isLastAssistant={idx === messages.length - 1}
                        isTyping={isTyping}
                        variant="markdown"
                        streamingFromWs={false}
                        onToolUiResult={handleToolUiResult}
                        onQuickReply={(text) => void handleSend(text)}
                      />
                    ) : (
                      <>
                        {msg.content}
                        {isTyping && idx === messages.length - 1 && (
                          <span className="typewriter-cursor" />
                        )}
                      </>
                    )}
                  </div>
                  <div className="flex items-center justify-between gap-2 mt-1">
                    <p className="text-xs opacity-70">
                      {new Date(msg.timestamp).toLocaleTimeString()}
                    </p>
                    {msg.role === "assistant" && msg.source && (
                      <span className="text-[10px] text-slate-500" title={msg.source === "L3" ? "Layer 3 直连大模型" : "Layer 2 兜底"}>
                        via {msg.source}
                      </span>
                    )}
                  </div>
                </div>
              </div>
            ))}
            {(isLoading || isTyping) && (
              <div className="flex justify-start">
                <div className="bg-slate-700 rounded-lg px-4 py-2 flex items-center gap-2">
                  <Loader2 className="w-4 h-4 animate-spin" />
                  <span className="text-slate-300">
                    {isLoading ? "思考中..." : "正在输入..."}
                  </span>
                </div>
              </div>
            )}
          </>
        )}
        <div ref={messagesEndRef} />
      </div>
        </div>
        {activeSkillCanvas ? (
          <div className="flex min-h-0 min-w-[280px] flex-1 flex-col overflow-hidden border-l border-purple-500/25">
            <SkillCanvasPane
              key={`${activeSkillCanvas.toolCallId ?? ""}-${activeSkillCanvas.toolName}`}
              active={activeSkillCanvas}
              onToolUiResult={handleToolUiResult}
              onRequestClose={handleDismissSkillCanvas}
            />
          </div>
        ) : null}
      </div>

      {/* 输入区域 */}
      <div className="border-t border-purple-500/20 p-4">
        {backgroundTaskPulse && (
          <div
            className="mb-3 rounded-lg border border-emerald-500/35 bg-slate-900/95 px-3 py-2 text-[11px] leading-tight text-slate-200 shadow-md"
            role="status"
            aria-live="polite"
          >
            <div className="mb-0.5 flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
              <span className="shrink-0 font-medium text-emerald-300/95">后台任务执行中</span>
              <code className="shrink-0 rounded bg-slate-800/90 px-1.5 py-0.5 text-[10px] text-emerald-200/90">
                {backgroundTaskPulse.taskId}
              </code>
            </div>
            <div className="min-h-[1.1em] overflow-hidden font-mono text-[13px] tracking-wider text-emerald-400/95">
              {backgroundTaskPulse.line.length > 0 ? backgroundTaskPulse.line : "\u00a0"}
            </div>
          </div>
        )}
        {zombieTasksPending && zombieTasksPending.count > 0 && (
          <div className="mb-3 rounded-lg border border-rose-500/45 bg-rose-950/90 px-3 py-2 text-xs text-rose-100">
            <p className="mb-1 flex flex-wrap items-center gap-2 font-medium text-rose-50">
              <span className="inline-flex min-h-[1.25rem] min-w-[1.25rem] items-center justify-center rounded-full bg-rose-500 px-1.5 text-[11px] font-bold text-rose-950">
                {zombieTasksPending.count}
              </span>
              上次断电或崩溃未闭环的后台任务
            </p>
            <ul className="mb-2 max-h-24 overflow-y-auto text-[11px] text-rose-100/85">
              {zombieTasksPending.tasks.slice(0, 5).map((t, i) => (
                <li key={`${t.task_id ?? "z"}-${i}`} className="truncate py-0.5 font-mono">
                  {t.task_id ?? "?"}
                  {(t.task_prompt ?? "").trim() ? ` — ${(t.task_prompt ?? "").slice(0, 80)}` : ""}
                </li>
              ))}
            </ul>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                className="rounded bg-rose-500/90 px-2 py-0.5 text-[11px] text-rose-950"
                onClick={() => {
                  setInput(
                    "请调用 core:check_interrupted_tasks，列出上次断电遗留的后台任务摘要，并问我是否需要用 core:submit_background_task 重新排队执行。",
                  );
                  dismissZombieTasksPending();
                }}
              >
                填入追问指令
              </button>
              <button type="button" className="text-[11px] text-rose-200/80" onClick={() => dismissZombieTasksPending()}>
                知道了
              </button>
            </div>
          </div>
        )}
        {/* 清空消息按钮（仅在消息存在时显示） */}
        {messages.length > 0 && (
          <div className="mb-2 flex justify-end">
            <button
              onClick={handleClearMessages}
              className="text-xs text-slate-400 hover:text-slate-300 flex items-center gap-1 px-2 py-1 rounded transition-colors"
              title="清空消息"
            >
              <Trash2 className="w-3 h-3" />
              <span>清空</span>
            </button>
          </div>
        )}

        {/* 录音状态提示 */}
        {recordingStatus && (
          <div className={`mb-2 text-xs px-3 py-1.5 rounded ${
            recordingStatus.includes('错误') || recordingStatus.includes('失败')
              ? 'bg-red-500/20 text-red-300'
              : 'bg-purple-500/20 text-purple-300'
          }`}>
            {recordingStatus}
          </div>
        )}

        <div className="flex gap-2">
          {/* 语音录制按钮 */}
          {/* VAD 语音采集（智能截断）测试按钮 */}
          <button
            onClick={isVoiceCaptureRunning ? stopVoiceCapture : startVoiceCapture}
            className={cn(
              "px-3 py-2 rounded-lg transition-colors flex items-center gap-1.5 text-xs",
              isVoiceCaptureRunning
                ? "bg-amber-600 hover:bg-amber-700 text-white"
                : "bg-slate-600 hover:bg-slate-500 text-slate-200"
            )}
            title={isVoiceCaptureRunning ? "停止 VAD 采集" : "开始 VAD 采集（说完自动截断）"}
          >
            {isVoiceCaptureRunning ? (
              <MicOff className="w-3.5 h-3.5" />
            ) : (
              <Mic className="w-3.5 h-3.5" />
            )}
            <span>VAD</span>
          </button>
          {/* 原有语音录制按钮 */}
          <button
            onClick={isRecording ? stopRecording : startRecording}
            disabled={!isConnected || isLoading}
            className={`px-4 py-2 rounded-lg transition-colors flex items-center gap-2 ${
              isRecording
                ? 'bg-red-600 hover:bg-red-700 animate-pulse'
                : 'bg-slate-600 hover:bg-slate-500 disabled:bg-slate-600 disabled:cursor-not-allowed'
            }`}
            title={isRecording ? "停止录音" : "开始语音录制"}
          >
            {isRecording ? (
              <>
                <Square className="w-4 h-4" />
                <span className="text-xs">停止</span>
              </>
            ) : (
              <Mic className="w-4 h-4" />
            )}
          </button>

          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyPress={handleKeyPress}
            placeholder={
              sensoryConnected ? "输入消息（L3 直连）..." :
              isConnected ? "输入消息（L2 兜底）..." : "等待 L3 或 L2 连接..."
            }
            disabled={(!sensoryConnected && !isConnected) || isLoading || isRecording || isTyping}
            className="flex-1 bg-slate-700/50 border border-purple-500/20 rounded-lg px-4 py-2 text-white placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-purple-500 resize-none disabled:opacity-50"
            rows={1}
            style={{ minHeight: "40px", maxHeight: "120px" }}
          />
          <button
            onClick={() => void handleSend()}
            disabled={(!sensoryConnected && !isConnected) || isLoading || isRecording || isTyping || !input.trim()}
            className="px-4 py-2 bg-purple-600 hover:bg-purple-700 disabled:bg-slate-600 disabled:cursor-not-allowed rounded-lg transition-colors flex items-center gap-2"
          >
            {isLoading ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Send className="w-4 h-4" />
            )}
          </button>
        </div>

        {/* 隐藏的音频元素用于播放语音回复 */}
        <audio ref={chatAudioRef} style={{ display: 'none' }} />
      </div>
    </div>
  );
}
