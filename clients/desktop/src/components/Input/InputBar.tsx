/**
 * InputBar - 输入胶囊组件
 * 
 * Aero Prism 风格的输入栏
 * - 全圆角胶囊形状
 * - 磨砂玻璃效果
 * - 文本和语音双模式输入
 * - 语音输入时显示声波可视化
 */

import React, { useState, useRef, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Mic, Send, Plus, Square } from "lucide-react";
import { cn } from "../../utils/cn";

interface InputBarProps {
  onSend?: (message: string) => void;
  onVoiceStart?: () => void;
  onVoiceEnd?: (audioBlob: Blob) => void;
  disabled?: boolean;
  placeholder?: string;
}

export const InputBar: React.FC<InputBarProps> = ({
  onSend,
  onVoiceStart,
  onVoiceEnd,
  disabled = false,
  placeholder = "输入消息...",
}) => {
  const [input, setInput] = useState("");
  const [isRecording, setIsRecording] = useState(false);
  const [audioLevels, setAudioLevels] = useState<number[]>([]);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const audioContextRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const animationFrameRef = useRef<number | null>(null);
  const streamRef = useRef<MediaStream | null>(null);

  // 生成声波可视化数据
  useEffect(() => {
    if (isRecording && analyserRef.current) {
      const bufferLength = analyserRef.current.frequencyBinCount;
      const dataArray = new Uint8Array(bufferLength);

      const updateAudioLevels = () => {
        if (!analyserRef.current) return;

        analyserRef.current.getByteFrequencyData(dataArray);
        
        // 提取低频数据用于可视化（简化版，只显示 5 个竖条）
        const levels: number[] = [];
        const step = Math.floor(bufferLength / 5);
        for (let i = 0; i < 5; i++) {
          const index = i * step;
          const value = dataArray[index] / 255; // 归一化到 0-1
          levels.push(Math.max(0.2, value)); // 最小高度 20%
        }
        
        setAudioLevels(levels);
        animationFrameRef.current = requestAnimationFrame(updateAudioLevels);
      };

      updateAudioLevels();
    } else {
      if (animationFrameRef.current) {
        cancelAnimationFrame(animationFrameRef.current);
        animationFrameRef.current = null;
      }
      setAudioLevels([]);
    }

    return () => {
      if (animationFrameRef.current) {
        cancelAnimationFrame(animationFrameRef.current);
      }
    };
  }, [isRecording]);

  // 开始录音
  const startRecording = async () => {
    try {
      // 如果已经在录音，先停止
      if (isRecording && mediaRecorderRef.current) {
        stopRecording();
        return;
      }

      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream; // 保存 stream 引用
      
      const mediaRecorder = new MediaRecorder(stream);
      mediaRecorderRef.current = mediaRecorder;
      audioChunksRef.current = [];

      // 创建音频分析器用于可视化
      const audioContext = new (window.AudioContext || (window as any).webkitAudioContext)();
      const analyser = audioContext.createAnalyser();
      const source = audioContext.createMediaStreamSource(stream);
      source.connect(analyser);
      analyser.fftSize = 256;

      audioContextRef.current = audioContext;
      analyserRef.current = analyser;

      mediaRecorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          audioChunksRef.current.push(event.data);
        }
      };

      mediaRecorder.onstop = () => {
        // 清理资源
        if (streamRef.current) {
          streamRef.current.getTracks().forEach((track) => track.stop());
          streamRef.current = null;
        }
        if (audioContextRef.current) {
          audioContextRef.current.close();
          audioContextRef.current = null;
        }
        analyserRef.current = null;
        
        // 创建音频 blob 并调用回调
        const audioBlob = new Blob(audioChunksRef.current, { type: "audio/wav" });
        onVoiceEnd?.(audioBlob);
        
        // 清理引用
        mediaRecorderRef.current = null;
        audioChunksRef.current = [];
      };

      mediaRecorder.start();
      setIsRecording(true);
      onVoiceStart?.();
    } catch (error) {
      console.error("Failed to start recording:", error);
      setIsRecording(false);
    }
  };

  // 停止录音
  const stopRecording = () => {
    // 立即更新 UI 状态
    setIsRecording(false);
    
    // 停止 MediaRecorder
    if (mediaRecorderRef.current) {
      try {
        if (mediaRecorderRef.current.state === "recording") {
          mediaRecorderRef.current.stop();
        } else if (mediaRecorderRef.current.state === "paused") {
          mediaRecorderRef.current.stop();
        }
      } catch (error) {
        console.error("Error stopping MediaRecorder:", error);
      }
    }
    
    // 如果 MediaRecorder 没有正确停止，强制清理资源
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((track) => {
        track.stop();
      });
      streamRef.current = null;
    }
    
    if (audioContextRef.current) {
      try {
        audioContextRef.current.close();
      } catch (error) {
        console.error("Error closing AudioContext:", error);
      }
      audioContextRef.current = null;
    }
    
    analyserRef.current = null;
    
    // 取消动画帧
    if (animationFrameRef.current) {
      cancelAnimationFrame(animationFrameRef.current);
      animationFrameRef.current = null;
    }
  };

  // 发送消息
  const handleSend = () => {
    if (input.trim() && !disabled) {
      onSend?.(input.trim());
      setInput("");
    }
  };

  // 键盘事件
  const handleKeyPress = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <motion.div
      className="h-14 bg-black/60 backdrop-blur-xl rounded-full border border-white/10 flex items-center px-4 gap-3 shadow-2xl"
      data-tauri-drag-region
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
      style={{ userSelect: "none" }}
    >
      {/* 语音按钮 */}
      <motion.button
        className={cn(
          "w-10 h-10 rounded-full flex items-center justify-center transition-colors",
          isRecording
            ? "bg-red-500 text-white"
            : "bg-white/10 text-gray-300 hover:bg-white/20"
        )}
        onClick={isRecording ? stopRecording : startRecording}
        disabled={disabled}
        whileHover={{ scale: 1.05 }}
        whileTap={{ scale: 0.95 }}
        animate={
          isRecording
            ? {
                scale: [1, 1.2, 1],
                boxShadow: [
                  "0 0 0px rgba(239,68,68,0.4)",
                  "0 0 20px rgba(239,68,68,0.6)",
                  "0 0 0px rgba(239,68,68,0.4)",
                ],
              }
            : {}
        }
        transition={{ duration: 1, repeat: isRecording ? Infinity : 0 }}
        onMouseDown={(e) => e.stopPropagation()}
      >
        {isRecording ? <Square className="w-4 h-4" /> : <Mic className="w-4 h-4" />}
      </motion.button>

      {/* 输入框或声波可视化 */}
      <div className="flex-1 flex items-center min-w-0 px-2">
        <AnimatePresence mode="wait">
          {isRecording ? (
            // 声波可视化
            <motion.div
              key="visualizer"
              className="flex items-center justify-center gap-1.5 h-8 w-full"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
            >
              {audioLevels.length > 0 ? (
                audioLevels.map((level, index) => (
                  <motion.div
                    key={index}
                    className="w-1.5 bg-gradient-to-t from-red-500 to-red-400 rounded-full"
                    style={{ height: `${Math.max(20, level * 100)}%` }}
                    animate={{
                      height: `${Math.max(20, level * 100)}%`,
                    }}
                    transition={{
                      duration: 0.1,
                      ease: "easeOut",
                    }}
                  />
                ))
              ) : (
                // 占位符竖条
                Array.from({ length: 5 }).map((_, index) => (
                  <motion.div
                    key={index}
                    className="w-1.5 bg-gradient-to-t from-red-500/60 to-red-400/60 rounded-full"
                    style={{ height: "40%" }}
                    animate={{
                      height: ["20%", "60%", "20%"],
                    }}
                    transition={{
                      duration: 0.6,
                      repeat: Infinity,
                      delay: index * 0.1,
                      ease: "easeInOut",
                    }}
                  />
                ))
              )}
            </motion.div>
          ) : (
            // 文本输入框
            <motion.input
              key="input"
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyPress={handleKeyPress}
              placeholder={placeholder}
              disabled={disabled}
              className="flex-1 bg-transparent border-none outline-none text-white placeholder:text-gray-400 text-sm"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onMouseDown={(e) => e.stopPropagation()}
            />
          )}
        </AnimatePresence>
      </div>

      {/* 发送按钮或附件按钮 */}
      {input.trim() ? (
        <motion.button
          className="w-10 h-10 rounded-full bg-purple-600 text-white flex items-center justify-center hover:bg-purple-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          onClick={handleSend}
          disabled={disabled}
          whileHover={{ scale: 1.05 }}
          whileTap={{ scale: 0.95 }}
          onMouseDown={(e) => e.stopPropagation()}
        >
          <Send className="w-4 h-4" />
        </motion.button>
      ) : (
        <motion.button
          className="w-10 h-10 rounded-full bg-white/10 text-gray-300 flex items-center justify-center hover:bg-white/20 transition-colors"
          whileHover={{ scale: 1.05 }}
          whileTap={{ scale: 0.95 }}
          onMouseDown={(e) => e.stopPropagation()}
        >
          <Plus className="w-4 h-4" />
        </motion.button>
      )}
    </motion.div>
  );
};
