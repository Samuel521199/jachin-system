/**
 * HolographicChat — 科幻全息风格聊天 UI
 *
 * - Mini Mode: 半透明发光球体，呼吸动画表示在线，可拖动，点击展开
 * - Expanded Mode: 无边框毛玻璃面板，HUD 角线，半透明渐变气泡，极简发光底线输入
 * - Transition: Framer Motion layout 形变（圆球 ↔ 矩形面板）
 */

import React, { useState, useRef, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Send, Mic, Sparkles, Minimize2, MessageSquare, Loader2, Square } from "lucide-react";
import { sendChatMessage, streamChatMessage, voiceChat } from "../../lib/api";
import { useAppStore } from "../../store/appStore";
import { useSpriteStore } from "../../store/spriteStore";
import {
  loadMessages,
  saveMessages,
  addMessage,
  StoredMessage,
} from "../../utils/messageStorage";
import { typewriterAnimation } from "../../utils/typewriter";

const LAYOUT_ID = "holographic-chat";
const ORB_SIZE = 56;
const PANEL_WIDTH = 380;
const PANEL_HEIGHT = 500;

export const HolographicChat: React.FC = () => {
  const [isOpen, setIsOpen] = useState(false);
  const [position, setPosition] = useState(() =>
    typeof window !== "undefined"
      ? { x: window.innerWidth - ORB_SIZE - 24, y: window.innerHeight - ORB_SIZE - 24 }
      : { x: 0, y: 0 }
  );
  const [messages, setMessages] = useState<StoredMessage[]>([]);
  const [inputValue, setInputValue] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [isTyping, setIsTyping] = useState(false);
  const [isRecording, setIsRecording] = useState(false);
  const [recordingStatus, setRecordingStatus] = useState("");
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const chatAudioRef = useRef<HTMLAudioElement | null>(null);
  const { isConnected } = useAppStore();
  const { setState } = useSpriteStore();

  useEffect(() => {
    const saved = loadMessages();
    if (saved.length > 0) setMessages(saved);
  }, []);

  useEffect(() => {
    if (messages.length > 0) saveMessages(messages);
  }, [messages]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isTyping]);

  const toggleOpen = () => setIsOpen(!isOpen);

  const handleSend = async () => {
    if (!inputValue.trim() || isLoading || !isConnected || isTyping) return;

    const userMessage: StoredMessage = {
      role: "user",
      content: inputValue.trim(),
      timestamp: Date.now(),
    };
    setMessages((prev) => addMessage(prev, userMessage));
    setInputValue("");
    setIsLoading(true);
    setState("thinking");

    const assistantMessage: StoredMessage = {
      role: "assistant",
      content: "",
      timestamp: Date.now(),
    };
    setMessages((prev) => addMessage(prev, assistantMessage));
    setIsTyping(true);
    setState("speaking");

    try {
      try {
        const fullReply = await streamChatMessage(userMessage.content, (chunk) => {
          setMessages((prev) => {
            const updated = [...prev];
            const last = updated[updated.length - 1];
            if (last?.role === "assistant") {
              updated[updated.length - 1] = { ...last, content: last.content + chunk };
            }
            return updated;
          });
        });
        setMessages((prev) => {
          const updated = [...prev];
          updated[updated.length - 1] = { ...updated[updated.length - 1], content: fullReply };
          saveMessages(updated);
          return updated;
        });
      } catch {
        const response = await sendChatMessage(userMessage.content);
        setMessages((prev) => {
          const updated = [...prev];
          updated[updated.length - 1] = { ...updated[updated.length - 1], content: response.reply };
          saveMessages(updated);
          return updated;
        });
      }
    } catch (error) {
      const errMsg: StoredMessage = {
        role: "assistant",
        content: `错误: ${error instanceof Error ? error.message : "未知错误"}`,
        timestamp: Date.now(),
      };
      setMessages((prev) => addMessage(prev, errMsg));
    } finally {
      setIsLoading(false);
      setIsTyping(false);
      setState("idle");
    }
  };

  const startRecording = async () => {
    if (!isConnected) {
      setRecordingStatus("请先连接后端服务");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mediaRecorder = new MediaRecorder(stream);
      mediaRecorderRef.current = mediaRecorder;
      audioChunksRef.current = [];
      setIsRecording(true);
      setRecordingStatus("正在录音...");
      setState("listening");

      mediaRecorder.ondataavailable = (e) => {
        if (e.data.size > 0) audioChunksRef.current.push(e.data);
      };
      mediaRecorder.onstop = () => {
        const blob = new Blob(audioChunksRef.current, { type: "audio/wav" });
        stream.getTracks().forEach((t) => t.stop());
        setIsRecording(false);
        setRecordingStatus("录音完成，正在处理...");
        handleVoiceChat(blob);
      };
      mediaRecorder.start();
    } catch (e: unknown) {
      setIsRecording(false);
      setRecordingStatus(`无法访问麦克风: ${e instanceof Error ? e.message : "未知错误"}`);
      setState("idle");
    }
  };

  const stopRecording = () => {
    if (mediaRecorderRef.current?.state !== "inactive") {
      mediaRecorderRef.current.stop();
    }
  };

  const handleVoiceChat = async (audioBlob: Blob) => {
    setIsLoading(true);
    setState("thinking");
    try {
      const file = new File([audioBlob], "recording.wav", { type: "audio/wav" });
      const response = await voiceChat(file, "wav", "zh-CN", true, "zh-CN-XiaoxiaoNeural");

      const userMessage: StoredMessage = {
        role: "user",
        content: `🎤 [语音] ${response.user_text || response.text || "已发送语音消息"}`,
        timestamp: Date.now(),
      };
      setMessages((prev) => addMessage(prev, userMessage));

      const assistantMessage: StoredMessage = {
        role: "assistant",
        content: "",
        timestamp: Date.now(),
      };
      setMessages((prev) => addMessage(prev, assistantMessage));
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
        const bytes = Uint8Array.from(atob(response.audio_base64), (c) => c.charCodeAt(0));
        const blob = new Blob([bytes], { type: "audio/wav" });
        chatAudioRef.current.src = URL.createObjectURL(blob);
        setState("speaking");
        await chatAudioRef.current.play();
      }
      setRecordingStatus("");
      setState("idle");
    } catch (error) {
      const errMsg: StoredMessage = {
        role: "assistant",
        content: `错误: ${error instanceof Error ? error.message : "语音处理失败"}`,
        timestamp: Date.now(),
      };
      setMessages((prev) => addMessage(prev, errMsg));
      setRecordingStatus("");
      setState("idle");
    } finally {
      setIsLoading(false);
      setIsTyping(false);
    }
  };

  const displayMessages = messages.filter((m) => m.role === "user" || m.role === "assistant");

  return (
    <>
      <audio ref={chatAudioRef} style={{ display: "none" }} />

      <div
        className="fixed z-50 flex flex-col items-end"
        style={{ left: position.x, top: position.y }}
      >
        <motion.div
          className="cursor-grab active:cursor-grabbing touch-none"
          drag
          dragMomentum={false}
          dragElastic={0}
          onDragEnd={(_, info) => {
            setPosition((p) => ({ x: p.x + info.offset.x, y: p.y + info.offset.y }));
          }}
        >
        <AnimatePresence mode="wait">
          {isOpen ? (
            <motion.div
              key="expanded"
              layoutId={LAYOUT_ID}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ type: "spring", stiffness: 300, damping: 30 }}
              className="flex flex-col overflow-hidden rounded-2xl touch-none"
              style={{
                width: PANEL_WIDTH,
                height: PANEL_HEIGHT,
                boxShadow: "0 0 40px rgba(0, 180, 255, 0.15), inset 0 0 0 1px rgba(255,255,255,0.06)",
              }}
              onClick={(e) => e.stopPropagation()}
            >
              {/* 背景：极低透明度 + 毛玻璃 */}
              <div
                className="absolute inset-0"
                style={{
                  backgroundColor: "rgba(5, 10, 25, 0.35)",
                  backdropFilter: "blur(20px)",
                  WebkitBackdropFilter: "blur(20px)",
                }}
              />
              {/* 边缘微光 */}
              <div className="absolute inset-0 rounded-2xl pointer-events-none ring-1 ring-cyan-500/20 ring-inset" />
              {/* HUD 角线 */}
              <div className="absolute top-0 left-0 w-4 h-4 border-t border-l border-cyan-400/70 pointer-events-none rounded-tl-2xl" />
              <div className="absolute top-0 right-0 w-4 h-4 border-t border-r border-cyan-400/70 pointer-events-none rounded-tr-2xl" />
              <div className="absolute bottom-0 left-0 w-4 h-4 border-b border-l border-violet-400/60 pointer-events-none rounded-bl-2xl" />
              <div className="absolute bottom-0 right-0 w-4 h-4 border-b border-r border-violet-400/60 pointer-events-none rounded-br-2xl" />
              <div className="absolute top-0 left-1/2 w-24 h-px bg-gradient-to-r from-transparent via-cyan-400/50 to-transparent pointer-events-none" />
              <div className="absolute bottom-0 left-1/2 w-24 h-px bg-gradient-to-r from-transparent via-violet-400/50 to-transparent pointer-events-none" />

              <div className="relative flex flex-col h-full p-4 text-cyan-50/95">
                {/* Header */}
                <div className="flex justify-between items-center mb-3 border-b border-white/5 pb-2">
                  <div className="flex items-center gap-2">
                    <Sparkles className="w-4 h-4 text-cyan-400/90 animate-pulse" />
                    <span className="font-semibold tracking-wider text-xs uppercase text-cyan-100/80">MIND STREAM</span>
                  </div>
                  <button
                    type="button"
                    onClick={toggleOpen}
                    className="p-1.5 rounded-full hover:bg-white/10 text-white/50 hover:text-white transition-colors"
                    aria-label="收起"
                  >
                    <Minimize2 className="w-4 h-4" />
                  </button>
                </div>

                {/* 消息区 — 半透明渐变气泡 */}
                <div className="flex-1 overflow-y-auto space-y-3 pr-1 min-h-0 scrollbar-thin scrollbar-thumb-white/10 scrollbar-track-transparent">
                  {displayMessages.map((msg, idx) => (
                    <motion.div
                      key={`${msg.timestamp}-${idx}`}
                      initial={{ opacity: 0, x: msg.role === "user" ? 12 : -12 }}
                      animate={{ opacity: 1, x: 0 }}
                      className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
                    >
                      <div
                        className={`max-w-[85%] p-3 rounded-2xl text-sm leading-relaxed relative overflow-hidden ${
                          msg.role === "user"
                            ? "rounded-tr-md bg-gradient-to-br from-cyan-500/25 to-blue-600/20 border border-cyan-400/20"
                            : "rounded-tl-md bg-gradient-to-br from-white/8 to-slate-500/10 border border-white/10"
                        }`}
                      >
                        <span className="relative z-10">
                          {msg.content}
                          {isTyping && idx === displayMessages.length - 1 && (
                            <span className="inline-block w-2 h-4 ml-1 bg-cyan-400/80 animate-pulse rounded-sm" />
                          )}
                        </span>
                        <div className="absolute inset-0 bg-gradient-to-b from-white/5 to-transparent pointer-events-none" />
                      </div>
                    </motion.div>
                  ))}
                  {(isLoading || isTyping) &&
                    displayMessages.length > 0 &&
                    displayMessages[displayMessages.length - 1]?.role !== "assistant" && (
                      <div className="flex justify-start">
                        <div className="px-3 py-2 rounded-2xl bg-white/5 border border-white/10 flex items-center gap-2">
                          <Loader2 className="w-3.5 h-3.5 text-cyan-400 animate-spin" />
                          <span className="text-xs text-slate-400">处理中...</span>
                        </div>
                      </div>
                    )}
                  <div ref={messagesEndRef} />
                </div>

                {recordingStatus && (
                  <div
                    className={`text-xs px-2 py-1 rounded mb-2 ${
                      recordingStatus.includes("错误") ? "bg-red-500/20 text-red-300" : "bg-cyan-500/15 text-cyan-300"
                    }`}
                  >
                    {recordingStatus}
                  </div>
                )}

                {/* 输入区：无底色，仅发光底线 */}
                <div className="flex items-center gap-2 mt-3">
                  <button
                    type="button"
                    onClick={isRecording ? stopRecording : startRecording}
                    disabled={!isConnected || isLoading}
                    className={`p-2 rounded-full border transition-all ${
                      isRecording
                        ? "bg-red-500/25 text-red-300 border-red-500/40"
                        : "bg-white/5 border-white/10 text-cyan-400/80 hover:bg-white/10"
                    }`}
                  >
                    {isRecording ? <Square className="w-4 h-4" /> : <Mic className="w-4 h-4" />}
                  </button>
                  <div className="flex-1 relative group">
                    <input
                      type="text"
                      value={inputValue}
                      onChange={(e) => setInputValue(e.target.value)}
                      onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && handleSend()}
                      placeholder={isConnected ? "输入指令..." : "等待连接..."}
                      disabled={!isConnected || isLoading}
                      className="w-full bg-transparent border-none py-2 pl-2 pr-2 text-sm focus:outline-none text-cyan-100 placeholder-cyan-100/40 disabled:opacity-50"
                    />
                    <div className="absolute bottom-0 left-0 h-px w-0 bg-gradient-to-r from-cyan-400 to-cyan-300 group-focus-within:w-full transition-all duration-500 shadow-[0_0_8px_rgba(34,211,238,0.5)]" />
                  </div>
                  <button
                    type="button"
                    onClick={handleSend}
                    disabled={!isConnected || isLoading || !inputValue.trim()}
                    className="p-2 rounded-full text-cyan-400 hover:text-cyan-200 hover:bg-cyan-500/20 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    <Send className="w-4 h-4" />
                  </button>
                </div>
              </div>
            </motion.div>
          ) : (
            <motion.button
              key="mini"
              type="button"
              layoutId={LAYOUT_ID}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ type: "spring", stiffness: 300, damping: 30 }}
              onClick={toggleOpen}
              className="relative flex items-center justify-center touch-none rounded-full focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-400/50"
              style={{
                width: ORB_SIZE,
                height: ORB_SIZE,
                boxShadow: "0 0 28px rgba(0, 220, 255, 0.35), 0 0 60px rgba(0, 180, 255, 0.15)",
              }}
              aria-label="打开聊天"
            >
              {/* 球体：半透明 + 毛玻璃 */}
              <motion.div
                className="absolute inset-0 rounded-full border border-cyan-400/30"
                style={{
                  backgroundColor: "rgba(8, 25, 45, 0.5)",
                  backdropFilter: "blur(12px)",
                  WebkitBackdropFilter: "blur(12px)",
                }}
                animate={{
                  scale: [1, 1.08, 1],
                  boxShadow: [
                    "0 0 20px rgba(0,220,255,0.3)",
                    "0 0 35px rgba(0,220,255,0.5)",
                    "0 0 20px rgba(0,220,255,0.3)",
                  ],
                }}
                transition={{
                  duration: 2.2,
                  repeat: Infinity,
                  ease: "easeInOut",
                }}
              />
              <MessageSquare className="w-6 h-6 text-cyan-100/95 relative z-10 drop-shadow-[0_0_6px_rgba(0,255,255,0.6)]" />
            </motion.button>
          )}
        </AnimatePresence>
        </motion.div>
      </div>
    </>
  );
};

export default HolographicChat;
