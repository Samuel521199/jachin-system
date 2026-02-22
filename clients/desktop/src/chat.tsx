/**
 * Chat Window - 全息风格对话窗口（MIND STREAM）
 *
 * 独立 chat 窗口入口，使用 ChatUI 全息 UI。
 */

import React, { useState, useRef, useEffect } from "react";
import ReactDOM from "react-dom/client";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { sendChatMessage, streamChatMessage, voiceChat, synthesizeSpeech, routeIntent, checkHealth } from "./lib/api";
import { useAppStore } from "./store/appStore";
import { useSpriteStore } from "./store/spriteStore";
import { loadMessages, saveMessages, addMessage, StoredMessage } from "./utils/messageStorage";
import { extractCompleteSentences, createAudioQueue } from "./utils/streamingTts";
import { typewriterAnimation } from "./utils/typewriter";
import { ChatUI } from "./components/Chat/ChatUI";
import "./styles/globals.css";

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
  const { isConnected, setConnected } = useAppStore();
  const { setState, ttsEnabled, ttsVoice } = useSpriteStore();
  /** 安全指令协议：safe | warning(COMMAND) | danger(高风险待确认) */
  const [riskLevel, setRiskLevel] = useState<"safe" | "warning" | "danger">("safe");
  const [pendingHighRisk, setPendingHighRisk] = useState<{ text: string; strippedText: string } | null>(null);

  // Chat 为独立窗口，需自行轮询后端连接状态（与主窗口共享 Zustand 但各自挂载，此处主动检测）
  useEffect(() => {
    let mounted = true;
    const check = async () => {
      try {
        await checkHealth();
        if (mounted) setConnected(true);
      } catch {
        if (mounted) setConnected(false);
      }
    };
    check();
    const interval = setInterval(check, 5000);
    return () => {
      mounted = false;
      clearInterval(interval);
    };
  }, [setConnected]);

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

  /** 实际发送消息（在意图路由与高风险确认之后调用） */
  const doActualSend = async (content: string) => {
    const userMessage: StoredMessage = {
      role: "user",
      content,
      timestamp: Date.now(),
    };
    setMessages((prev) => addMessage(prev, userMessage));
    setInput("");
    setIsLoading(true);
    setRiskLevel("safe");
    setState("thinking");

    try {
      const assistantMessage: StoredMessage = {
        role: "assistant",
        content: "",
        timestamp: Date.now(),
      };
      setMessages((prev) => addMessage(prev, assistantMessage));
      setState("speaking");
      setIsTyping(true);

      let finalReply: string | null = null;
      let accumulatedForTts = "";

      const audioEl = chatAudioRef.current;
      const ttsQueue =
        ttsEnabled && audioEl
          ? createAudioQueue(audioEl, () => setState("idle"))
          : null;

      const pendingSynths: Promise<void>[] = [];
      const enqueueSentence = (sentence: string) => {
        if (!sentence.trim() || !ttsQueue) return;
        const p = synthesizeSpeech(sentence.trim(), ttsVoice)
          .then((blob) => {
            ttsQueue.enqueue(blob);
            setState("speaking");
          })
          .catch(() => {});
        pendingSynths.push(p);
      };

      try {
        finalReply = await streamChatMessage(userMessage.content, (chunk) => {
          accumulatedForTts += chunk;
          setMessages((prev) => {
            const updated = [...prev];
            const last = updated[updated.length - 1];
            if (last?.role === "assistant") {
              updated[updated.length - 1] = { ...last, content: last.content + chunk };
            }
            return updated;
          });
          const { complete, remainder } = extractCompleteSentences(accumulatedForTts);
          accumulatedForTts = remainder;
          for (const sentence of complete) {
            enqueueSentence(sentence);
          }
        });
        setMessages((prev) => {
          const updated = [...prev];
          updated[updated.length - 1] = { ...updated[updated.length - 1], content: finalReply ?? "" };
          saveMessages(updated);
          return updated;
        });
        if (accumulatedForTts.trim()) {
          enqueueSentence(accumulatedForTts);
        }
        Promise.all(pendingSynths).finally(() => ttsQueue?.ensureIdle());
      } catch {
        const response = await sendChatMessage(userMessage.content);
        finalReply = response.reply;
        setMessages((prev) => {
          const updated = [...prev];
          updated[updated.length - 1] = { ...updated[updated.length - 1], content: response.reply };
          saveMessages(updated);
          return updated;
        });
        if (ttsEnabled && finalReply && audioEl) {
          try {
            const audioBlob = await synthesizeSpeech(finalReply, ttsVoice);
            const audioUrl = URL.createObjectURL(audioBlob);
            audioEl.src = audioUrl;
            setState("speaking");
            audioEl.onended = () => {
              setState("idle");
              URL.revokeObjectURL(audioUrl);
            };
            await audioEl.play();
          } catch {
            setState("idle");
          }
        }
      }

      setIsTyping(false);

      if (!ttsQueue && !(ttsEnabled && finalReply && audioEl)) {
        setTimeout(() => setState("idle"), 3000);
      }
    } catch (error) {
      const errorMessage: StoredMessage = {
        role: "assistant",
        content: `错误: ${error instanceof Error ? error.message : "未知错误"}`,
        timestamp: Date.now(),
      };
      setMessages((prev) => addMessage(prev, errorMessage));
      setState("idle");
    } finally {
      setIsLoading(false);
      setIsTyping(false);
      setRiskLevel("safe");
    }
  };

  const handleSend = async () => {
    if (!input.trim() || isLoading || isTyping) return;
    const trimmed = input.trim();

    try {
      const routed = await routeIntent(trimmed);
      if (routed.intent_type === "COMMAND") {
        if (routed.risk_level === "high") {
          setRiskLevel("danger");
          setPendingHighRisk({ text: trimmed, strippedText: routed.stripped_text });
          return;
        }
        setRiskLevel("warning");
      } else {
        setRiskLevel("safe");
      }
    } catch {
      setRiskLevel("safe");
    }
    await doActualSend(trimmed);
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

  // 语音聊天处理
  const handleVoiceChat = async (audioBlob: Blob) => {
    setIsLoading(true);
    setRecordingStatus("正在处理语音...");
    setState("thinking");

    try {
      // 将 Blob 转换为 File
      const audioFile = new File([audioBlob], 'recording.wav', { type: 'audio/wav' });
      
      // 调用语音聊天API
      const response = await voiceChat(audioFile, 'wav', 'zh-CN', true, 'zh-CN-XiaoxiaoNeural');
      
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
        setState("speaking");
        
        // 播放完成后恢复待机状态
        chatAudioRef.current.onended = () => {
          setState("idle");
        };
        
        await chatAudioRef.current.play();
      } else {
        setState("idle");
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
      setState("idle");
    } finally {
      setIsLoading(false);
      setIsTyping(false);
    }
  };

  return (
    <div className="w-full h-full min-h-0 flex flex-col bg-transparent border-0 relative" style={{ height: "100vh", background: "transparent" }}>
      <ChatUI
        messages={messages}
        input={input}
        onInputChange={setInput}
        onSend={handleSend}
        onVoiceStart={startRecording}
        onVoiceStop={stopRecording}
        isLoading={isLoading}
        isTyping={isTyping}
        isRecording={isRecording}
        recordingStatus={recordingStatus}
        listeningText={listeningText}
        placeholder={isConnected ? "输入指令..." : "等待连接..."}
        riskLevel={riskLevel}
        disabled={!isConnected}
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
