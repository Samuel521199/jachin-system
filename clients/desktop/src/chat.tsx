/**
 * Chat Window - 全息风格对话窗口（MIND STREAM）
 *
 * 独立 chat 窗口入口，使用 ChatUI 全息 UI。
 */

import React, { useState, useRef, useEffect } from "react";
import ReactDOM from "react-dom/client";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { invoke } from "@tauri-apps/api/core";
import { voiceChat, synthesizeSpeech, voiceProcess, streamChatMessage, checkHealth, type VoiceProcessResponse } from "./lib/api";
import { useSpriteStore } from "./store/spriteStore";
import { useSttAudioReady } from "./hooks/useSttAudioReady";
import { useSensoryWebSocket } from "./hooks/useSensoryWebSocket";
import { loadMessages, saveMessages, addMessage, StoredMessage } from "./utils/messageStorage";
import { extractCompleteSentences, createAudioQueue } from "./utils/streamingTts";
import { typewriterAnimation } from "./utils/typewriter";
import { ChatUI } from "./components/Chat/ChatUI";
import { SensoryOverlay } from "./console/components/SensoryOverlay";
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
  const { streamingContent: wsStreamingContent, handoffEvent, swarmEvent, registerChunkHandler, registerAnswerHandler, registerStepHandler, sendInput } = sensory;
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

  // 窗口显示时请求焦点，便于左键点击能落到输入区
  useEffect(() => {
    getCurrentWindow().setFocus().catch(() => {});
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

  /** 实际发送消息：优先 L3 Sensory，未连接时直连 L2 文本 API（与语音同源） */
  const doActualSend = async (content: string) => {
    const userMessage: StoredMessage = { role: "user", content, timestamp: Date.now() };
    setMessages((prev) => addMessage(prev, userMessage));
    setInput("");
    setIsLoading(true);
    setRiskLevel("safe");
    setState("thinking");

    const assistantMessage: StoredMessage = { role: "assistant", content: "", timestamp: Date.now() };
    setMessages((prev) => addMessage(prev, assistantMessage));
    setIsTyping(true);

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
    const cleanup = (finalContent: string, source?: "L3" | "L2", opts?: { skipContentUpdate?: boolean }) => {
      if (timeoutCleared) return;
      timeoutCleared = true;
      clearTimeout(timeoutId);
      setMessages((prev) => {
        const updated = [...prev];
        const last = updated[updated.length - 1];
        if (last?.role === "assistant" && !opts?.skipContentUpdate) {
          updated[updated.length - 1] = { ...last, content: finalContent || last.content, source };
        } else if (last?.role === "assistant" && opts?.skipContentUpdate) {
          updated[updated.length - 1] = { ...last, source };
        }
        saveMessages(updated);
        return updated;
      });
      if (ttsQueue && finalContent) {
        const { complete, remainder } = extractCompleteSentences(accumulatedForTts + finalContent);
        complete.forEach(enqueueSentence);
        if (remainder.trim()) enqueueSentence(remainder);
        ttsQueue.ensureIdle();
      }
      setIsLoading(false);
      setIsTyping(false);
      setRiskLevel("safe");
      if (!ttsQueue) setTimeout(() => setState("idle"), 2000);
    };

    const chunkHandler = (chunk: string) => {
      accumulatedForTts += chunk;
      setMessages((prev) => {
        const updated = [...prev];
        const last = updated[updated.length - 1];
        if (last?.role === "assistant") {
          updated[updated.length - 1] = { ...last, content: last.content + chunk };
        }
        return updated;
      });
    };

    const stepHandler = (stepType: string, content: string) => {
      setMessages((prev) => {
        const updated = [...prev];
        const last = updated[updated.length - 1];
        if (last?.role === "assistant") {
          updated[updated.length - 1] = { ...last, content: last.content + content };
        }
        return updated;
      });
    };

    const timeoutId = setTimeout(() => {
      registerChunkHandler(null);
      registerAnswerHandler(null);
      registerStepHandler(null);
      setMessages((prev) => {
        const last = prev[prev.length - 1];
        if (last?.role === "assistant" && !last.content) {
          return [...prev.slice(0, -1), { ...last, content: "响应超时（120 秒），请检查 Layer 3 或 Layer 2 是否正常运行" }];
        }
        return prev;
      });
      setIsLoading(false);
      setIsTyping(false);
    }, 120000);

    const pendingL3InputRef = { current: content };
    registerChunkHandler(chunkHandler);
    registerStepHandler(stepHandler);
    registerAnswerHandler((answerContent) => {
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
            registerChunkHandler(null);
            registerAnswerHandler(null);
            registerStepHandler(null);
            cleanup(fullText, "L2");
          })
          .catch((e) => {
            registerChunkHandler(null);
            registerAnswerHandler(null);
            registerStepHandler(null);
            cleanup(`L2 兜底也失败：${(e as Error).message}`, "L2");
          });
      } else {
        cleanup(answerContent, "L3", { skipContentUpdate: true });
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

    // L2 兜底
    try {
      console.debug("[Chat] L2 streamChatMessage 开始");
      const fullText = await streamChatMessage(content, (chunk) => chunkHandler(chunk));
      registerChunkHandler(null);
      registerAnswerHandler(null);
      cleanup(fullText, "L2");
    } catch (e) {
      console.debug("[Chat] L2 streamChatMessage 失败:", (e as Error).message);
      clearTimeout(timeoutId);
      registerChunkHandler(null);
      registerAnswerHandler(null);
      setMessages((prev) => {
        const updated = [...prev];
        const last = updated[updated.length - 1];
        if (last?.role === "assistant" && !last.content) {
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
      setMessages((prev) => addMessage(prev, { role: "assistant", content: "", timestamp: Date.now() }));
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

  // v8.0 流式神经：WebSocket chunk 追加到当前 Assistant 消息
  const appendChunkRef = useRef<(chunk: string) => void>(() => {});
  const isLoadingRef = useRef(false);
  isLoadingRef.current = isLoading;
  appendChunkRef.current = (chunk: string) => {
    if (!isLoadingRef.current) return;
    setMessages((prev) => {
      const last = prev[prev.length - 1];
      if (last?.role === "assistant") {
        return prev.map((m, i) =>
          i === prev.length - 1 ? { ...m, content: m.content + chunk } : m
        );
      }
      return prev;
    });
  };
  useEffect(() => {
    registerChunkHandler((chunk) => appendChunkRef.current(chunk));
    return () => registerChunkHandler(null);
  }, [registerChunkHandler]);

  // v8.0 人格色彩：Handoff 时全局主题突变（default 科技蓝 / architect 赛博紫 / researcher 矩阵绿）
  const personaTheme = handoffEvent?.persona ?? "default";
  useEffect(() => {
    const root = document.getElementById("chat-root");
    if (root) root.setAttribute("data-persona", personaTheme);
  }, [personaTheme]);

  return (
    <div
      className="w-full h-full min-h-0 flex flex-col bg-transparent border-0 relative"
      style={{ height: "100vh", background: "transparent" }}
    >
      {/* v8.0 全息感官：Handoff + Swarm + HITL 等 */}
      <SensoryOverlay sensory={sensory} />
      <ChatUI
        messages={messages}
        input={input}
        onInputChange={setInput}
        onSend={handleSend}
        onVoiceStart={startRecording}
        onVoiceStop={stopRecording}
        isVadActive={isVadActive}
        onVadToggle={handleVadToggle}
        isLoading={isLoading}
        isTyping={isTyping}
        isRecording={isRecording}
        recordingStatus={recordingStatus}
        listeningText={listeningText}
        placeholder={
          sensory.connected ? "输入指令或语音（L3 直连）..." :
          l2Available ? "输入指令或语音（L2 兜底）..." :
          "等待 L3 (ws://localhost:18981) 或 L2 连接..."
        }
        riskLevel={riskLevel}
        disabled={!sensory.connected && !l2Available}
        streamingFromWs={!!(isTyping && wsStreamingContent)}
      />
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
      <audio ref={chatAudioRef} style={{ display: "none" }} />
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
