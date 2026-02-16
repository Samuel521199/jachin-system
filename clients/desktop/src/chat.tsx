/**
 * Chat Window - 全息风格对话窗口（MIND STREAM）
 * 
 * 独立 chat 窗口入口，使用 ChatWindow 全息 UI。
 */

import React, { useState, useRef, useEffect } from "react";
import ReactDOM from "react-dom/client";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { sendChatMessage, streamChatMessage, voiceChat, synthesizeSpeech } from "./lib/api";
import { useSpriteStore } from "./store/spriteStore";
import { loadMessages, saveMessages, addMessage, StoredMessage } from "./utils/messageStorage";
import { extractCompleteSentences, createAudioQueue } from "./utils/streamingTts";
import { typewriterAnimation } from "./utils/typewriter";
import { ChatWindow, ChatMessage } from "./components/Chat/ChatWindow";
import "./styles/globals.css";

function ChatApp() {
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
  const typewriterCancelRef = useRef<(() => void) | null>(null);
  const { setState, ttsEnabled, ttsVoice } = useSpriteStore();

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

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isTyping]);

  const handleSend = async () => {
    if (!input.trim() || isLoading || isTyping) return;

    const userMessage: StoredMessage = {
      role: "user",
      content: input.trim(),
      timestamp: Date.now(),
    };
    setMessages((prev) => addMessage(prev, userMessage));
    setInput("");
    setIsLoading(true);

    // 更新精灵状态为思考
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
    }
  };

  // 注意：handleClearMessages, handleClose, handleKeyPress 已移至 ChatWindow 组件

  // 开始录音
  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mediaRecorder = new MediaRecorder(stream);
      mediaRecorderRef.current = mediaRecorder;
      audioChunksRef.current = [];
      setIsRecording(true);
      setRecordingStatus("正在录音...");
      
      // 更新精灵状态为监听
      setState("listening");

      mediaRecorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          audioChunksRef.current.push(event.data);
        }
      };

      mediaRecorder.onstop = () => {
        const audioBlob = new Blob(audioChunksRef.current, { type: 'audio/wav' });
        (window as any).recordedAudioBlob = audioBlob;
        stream.getTracks().forEach(track => track.stop());
        setIsRecording(false);
        setRecordingStatus("录音完成");
        
        // 自动发送语音消息
        handleVoiceChat(audioBlob);
      };

      mediaRecorder.start();
    } catch (error: any) {
      setIsRecording(false);
      setRecordingStatus(`无法访问麦克风: ${error.message}`);
      setState("idle");
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

  // 转换消息格式（过滤 system，ChatWindow 仅支持 user | assistant）
  const chatMessages: ChatMessage[] = messages
    .filter((msg) => msg.role !== "system")
    .map((msg) => ({
      role: msg.role as "user" | "assistant",
      content: msg.content,
      timestamp: msg.timestamp ?? 0,
    }));

  return (
    <>
      <ChatWindow
        messages={chatMessages}
        input={input}
        onInputChange={setInput}
        onSend={handleSend}
        onVoiceStart={startRecording}
        onVoiceStop={stopRecording}
        isLoading={isLoading}
        isTyping={isTyping}
        isRecording={isRecording}
        placeholder="Type a message..."
      />
      {/* 隐藏的音频元素，用于播放 TTS（语音回复 + 文本回复朗读） */}
      <audio ref={chatAudioRef} style={{ display: "none" }} />
    </>
  );
}

// 初始化
const root = document.getElementById("chat-root");
if (root) {
  ReactDOM.createRoot(root).render(
    <React.StrictMode>
      <ChatApp />
    </React.StrictMode>
  );
}
