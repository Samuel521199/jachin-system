import { useState, useRef, useEffect } from "react";
import { Send, Loader2, Mic, Square, Trash2, MicOff } from "lucide-react";
import { invoke } from "@tauri-apps/api/core";
import { voiceChat } from "../lib/api";
import { useAppStore } from "../store/appStore";
import { useSttAudioReady } from "../hooks/useSttAudioReady";
import { useSensoryWebSocket } from "../hooks/useSensoryWebSocket";
import { cn } from "../utils/cn";
import { loadMessages, saveMessages, clearMessages, addMessage, StoredMessage } from "../utils/messageStorage";
import { typewriterAnimation } from "../utils/typewriter";

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
  const { isConnected } = useAppStore();
  const sensory = useSensoryWebSocket();
  const { connected: sensoryConnected, sendInput, registerChunkHandler, registerAnswerHandler } = sensory;
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

  // 自动保存消息
  useEffect(() => {
    if (messages.length > 0) {
      saveMessages(messages);
    }
  }, [messages]);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  const handleSend = async () => {
    if (!input.trim() || isLoading || isTyping) return;
    if (!sensoryConnected) {
      setMessages((prev) => addMessage(prev, {
        role: "assistant",
        content: "未连接 L3 引擎。请确认桌面端已启动（Tauri 会自动拉起 L3）。",
        timestamp: Date.now(),
      }));
      return;
    }

    const userMessage: StoredMessage = {
      role: "user",
      content: input.trim(),
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

    const timeoutId = setTimeout(() => {
      registerChunkHandler(null);
      registerAnswerHandler(null);
      setMessages((prev) => {
        const last = prev[prev.length - 1];
        if (last?.role === "assistant" && !last.content) {
          return [...prev.slice(0, -1), { ...last, content: "响应超时（120 秒）" }];
        }
        return prev;
      });
      setIsLoading(false);
      setIsTyping(false);
    }, 120000);

    const cleanup = (finalContent: string) => {
      clearTimeout(timeoutId);
      registerChunkHandler(null);
      registerAnswerHandler(null);
      setMessages((prev) => {
        const updated = [...prev];
        const last = updated[updated.length - 1];
        if (last?.role === "assistant") {
          updated[updated.length - 1] = { ...last, content: finalContent || last.content };
        }
        saveMessages(updated);
        return updated;
      });
      setIsLoading(false);
      setIsTyping(false);
    };

    const chunkHandler = (chunk: string) => {
      setMessages((prev) => {
        const updated = [...prev];
        const last = updated[updated.length - 1];
        if (last?.role === "assistant") {
          updated[updated.length - 1] = { ...last, content: last.content + chunk };
        }
        return updated;
      });
    };

    registerChunkHandler(chunkHandler);
    registerAnswerHandler((answerContent) => cleanup(answerContent));

    const sent = sendInput(userMessage.content);
    if (!sent) {
      clearTimeout(timeoutId);
      registerChunkHandler(null);
      registerAnswerHandler(null);
      setMessages((prev) => addMessage(prev, {
        role: "assistant",
        content: "发送失败：L3 未连接",
        timestamp: Date.now(),
      }));
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

  // 开始录音
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

  return (
    <div className="h-full flex flex-col bg-slate-800/50 rounded-lg border border-purple-500/20 overflow-hidden">
      {/* 消息区域 */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {messages.length === 0 ? (
          <div className="h-full flex items-center justify-center text-slate-400">
            <div className="text-center">
              <div className="text-4xl mb-4">🤖</div>
              <p>开始与 Jachin AI 助手对话</p>
              <p className="text-sm mt-2 text-slate-500">
                {isConnected
                  ? "已连接到后端服务"
                  : "等待连接后端服务..."}
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
                  <p className="whitespace-pre-wrap">
                    {msg.content}
                    {isTyping && idx === messages.length - 1 && (
                      <span className="typewriter-cursor" />
                    )}
                  </p>
                  <p className="text-xs mt-1 opacity-70">
                    {new Date(msg.timestamp).toLocaleTimeString()}
                  </p>
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

      {/* 输入区域 */}
      <div className="border-t border-purple-500/20 p-4">
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
              isConnected ? "输入消息..." : "等待连接..."
            }
            disabled={!isConnected || isLoading || isRecording || isTyping}
            className="flex-1 bg-slate-700/50 border border-purple-500/20 rounded-lg px-4 py-2 text-white placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-purple-500 resize-none disabled:opacity-50"
            rows={1}
            style={{ minHeight: "40px", maxHeight: "120px" }}
          />
          <button
            onClick={handleSend}
            disabled={!isConnected || isLoading || isRecording || isTyping || !input.trim()}
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
