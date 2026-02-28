/**
 * WakeModePanel - 唤醒模式界面
 *
 * 启动/停止唤醒词监听，设置唤醒词或名字，只有说出该提示语/名字时才会激活 AI 助手。
 * 支持模拟唤醒测试。
 */

import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { Mic, MicOff, Play, Loader2, Save } from "lucide-react";
import { cn } from "../../utils/cn";

const DEFAULT_WAKE_WORD = "Jachin";

export interface UserSettingsWake {
  wake_word?: string | null;
}

export function WakeModePanel() {
  const [wakeWord, setWakeWord] = useState(DEFAULT_WAKE_WORD);
  const [savedWakeWord, setSavedWakeWord] = useState<string | null>(null);
  const [isListening, setIsListening] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [lastWakePayload, setLastWakePayload] = useState<{ wake_word?: string } | null>(null);
  const [isVoiceCaptureRunning, setIsVoiceCaptureRunning] = useState(false);

  const loadSettings = async () => {
    setLoading(true);
    try {
      const settings = await invoke<UserSettingsWake>("get_user_settings");
      const word = (settings?.wake_word?.trim() || DEFAULT_WAKE_WORD) as string;
      setWakeWord(word);
      setSavedWakeWord(word);
      const running = await invoke<boolean>("stt_wake_listener_running");
      setIsListening(running);
    } catch {
      setWakeWord(DEFAULT_WAKE_WORD);
      setSavedWakeWord(DEFAULT_WAKE_WORD);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadSettings();
  }, []);

  useEffect(() => {
    const unlisten = listen<{ wake_word?: string }>("WAKE_UP", (event) => {
      setLastWakePayload(event.payload ?? null);
    });
    return () => {
      unlisten.then((fn) => fn());
    };
  }, []);

  const refreshVoiceCaptureStatus = async () => {
    try {
      const running = await invoke<boolean>("is_voice_capture_running");
      setIsVoiceCaptureRunning(running);
    } catch {
      setIsVoiceCaptureRunning(false);
    }
  };

  useEffect(() => {
    refreshVoiceCaptureStatus();
  }, []);

  const handleStartVoiceCapture = async () => {
    try {
      await invoke("start_voice_capture");
      setIsVoiceCaptureRunning(true);
    } catch (e) {
      console.error("Start voice capture failed:", e);
    }
  };

  const handleStopVoiceCapture = async () => {
    try {
      await invoke("stop_voice_capture");
      setIsVoiceCaptureRunning(false);
    } catch (e) {
      console.error("Stop voice capture failed:", e);
    }
  };

  const handleSaveWakeWord = async () => {
    const word = wakeWord.trim() || DEFAULT_WAKE_WORD;
    setSaving(true);
    try {
      const settings = await invoke<UserSettingsWake>("get_user_settings");
      await invoke("update_user_settings", {
        patch: { ...settings, wake_word: word || null },
      });
      setSavedWakeWord(word);
    } finally {
      setSaving(false);
    }
  };

  const handleStartListening = async () => {
    const word = wakeWord.trim() || DEFAULT_WAKE_WORD;
    try {
      await invoke("stt_start_wake_listener", { wake_word: word || undefined });
      setIsListening(true);
    } catch (e) {
      console.error("Start wake listener failed:", e);
    }
  };

  const handleStopListening = async () => {
    try {
      await invoke("stt_stop_wake_listener");
      setIsListening(false);
    } catch (e) {
      console.error("Stop wake listener failed:", e);
    }
  };

  const handleSimulateWake = async () => {
    try {
      await invoke("stt_emit_wake_up");
    } catch (e) {
      console.error("Simulate wake failed:", e);
    }
  };

  if (loading) {
    return (
      <div className="h-full flex items-center justify-center p-6">
        <Loader2 className="w-8 h-8 animate-spin text-rose-400" />
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col p-6 overflow-auto">
      <header className="flex-shrink-0 mb-6">
        <h1
          className="font-sci-fi text-2xl font-bold text-transparent bg-clip-text bg-gradient-to-r from-cyan-400 to-rose-500"
          style={{ fontFamily: "Orbitron, sans-serif" }}
        >
          唤醒模式
        </h1>
        <p className="text-slate-500 text-sm mt-0.5">
          设置唤醒词或名字，只有说出该提示语时才会激活 AI 助手（模式 B：Wake-Up）
        </p>
      </header>

      <div className="flex-1 space-y-6">
        {/* VAD 放在最上方，进入页面即可见 */}
        <motion.section
          className="glass-panel rounded-xl overflow-hidden border border-amber-500/20"
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3 }}
        >
          <div className="px-4 py-3 border-b border-white/10 flex items-center gap-2 bg-amber-500/5">
            <span className="font-mono text-xs uppercase tracking-wider text-amber-400/90">
              VAD 语音采集（智能截断）
            </span>
            {isVoiceCaptureRunning && (
              <span className="ml-auto flex items-center gap-1.5 text-xs text-amber-400">
                <span className="w-2 h-2 rounded-full bg-amber-400 animate-pulse" />
                采集中
              </span>
            )}
          </div>
          <div className="p-4 space-y-2">
            <p className="text-xs text-slate-500">
              说完话约 0.8 秒静音后自动截断。需使用 <code className="text-cyan-400">npm run tauri:dev:ambient</code> 启动并已放置 silero_vad.onnx。
            </p>
            <div className="flex gap-3">
              {!isVoiceCaptureRunning ? (
                <button
                  type="button"
                  onClick={() => void handleStartVoiceCapture()}
                  className={cn(
                    "px-4 py-2.5 rounded-xl border border-amber-500/40 bg-amber-500/15 text-amber-300",
                    "hover:bg-amber-500/25 flex items-center gap-2 font-medium"
                  )}
                >
                  <Mic className="w-4 h-4" />
                  开始 VAD 采集
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => void handleStopVoiceCapture()}
                  className={cn(
                    "px-4 py-2.5 rounded-xl border border-rose-500/40 bg-rose-500/15 text-rose-300",
                    "hover:bg-rose-500/25 flex items-center gap-2 font-medium"
                  )}
                >
                  <MicOff className="w-4 h-4" />
                  停止 VAD 采集
                </button>
              )}
            </div>
          </div>
        </motion.section>

        <motion.section
          className="glass-panel rounded-xl overflow-hidden"
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3, delay: 0.05 }}
        >
          <div className="px-4 py-3 border-b border-white/10 flex items-center gap-2">
            <Mic className="w-4 h-4 text-cyan-400/80" />
            <span className="font-mono text-xs uppercase tracking-wider text-slate-400">
              唤醒词 / 名字
            </span>
          </div>
          <div className="p-4 space-y-3">
            <p className="text-xs text-slate-500">
              说出下面设置的词或名字即可激活助手（如「{savedWakeWord || wakeWord}」）。留空则使用默认「{DEFAULT_WAKE_WORD}」。
            </p>
            <div className="flex gap-2">
              <input
                type="text"
                value={wakeWord}
                onChange={(e) => setWakeWord(e.target.value)}
                onBlur={() => {
                  const w = wakeWord.trim() || DEFAULT_WAKE_WORD;
                  if (w !== savedWakeWord) void handleSaveWakeWord();
                }}
                placeholder={DEFAULT_WAKE_WORD}
                className={cn(
                  "flex-1 px-3 py-2 rounded border bg-white/5 text-slate-200 text-sm font-mono",
                  "border-white/10 focus:border-cyan-500/50 focus:ring-1 focus:ring-cyan-500/30",
                  "placeholder-slate-500"
                )}
              />
              <button
                type="button"
                onClick={() => void handleSaveWakeWord()}
                disabled={saving || (wakeWord.trim() || DEFAULT_WAKE_WORD) === savedWakeWord}
                className={cn(
                  "px-3 py-2 rounded border border-cyan-500/30 text-cyan-400 text-sm font-mono",
                  "hover:bg-cyan-500/15 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
                )}
              >
                {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                保存
              </button>
            </div>
          </div>
        </motion.section>

        <motion.section
          className="glass-panel rounded-xl overflow-hidden"
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3, delay: 0.05 }}
        >
          <div className="px-4 py-3 border-b border-white/10 flex items-center gap-2">
            <span className="font-mono text-xs uppercase tracking-wider text-slate-400">
              监听状态
            </span>
            {isListening && (
              <span className="ml-auto flex items-center gap-1.5 text-xs text-cyan-400">
                <span className="w-2 h-2 rounded-full bg-cyan-400 animate-pulse" />
                正在监听
              </span>
            )}
          </div>
          <div className="p-4 flex flex-wrap gap-3">
            {!isListening ? (
              <button
                type="button"
                onClick={() => void handleStartListening()}
                className={cn(
                  "px-4 py-2.5 rounded-xl border border-cyan-500/40 bg-cyan-500/15 text-cyan-300",
                  "hover:bg-cyan-500/25 flex items-center gap-2 font-medium"
                )}
              >
                <Mic className="w-4 h-4" />
                启动唤醒监听
              </button>
            ) : (
              <button
                type="button"
                onClick={() => void handleStopListening()}
                className={cn(
                  "px-4 py-2.5 rounded-xl border border-rose-500/40 bg-rose-500/15 text-rose-300",
                  "hover:bg-rose-500/25 flex items-center gap-2 font-medium"
                )}
              >
                <MicOff className="w-4 h-4" />
                停止监听
              </button>
            )}
            <button
              type="button"
              onClick={() => void handleSimulateWake()}
              className={cn(
                "px-4 py-2.5 rounded-xl border border-white/20 bg-white/5 text-slate-300",
                "hover:bg-white/10 flex items-center gap-2 font-medium"
              )}
            >
              <Play className="w-4 h-4" />
              模拟唤醒
            </button>
          </div>
          {lastWakePayload && (
            <div className="px-4 pb-4">
              <p className="text-xs text-slate-500">
                上次唤醒：通过「{lastWakePayload.wake_word ?? "—"}」触发
              </p>
            </div>
          )}
        </motion.section>

        <div className="text-xs text-slate-500 space-y-1">
          <p>· 当前 KWS 为占位实现，真实唤醒词识别需后续接入 openWakeWord 等引擎。</p>
          <p>· 使用「模拟唤醒」可测试前端收到 WAKE_UP 事件后的行为（如打开 Chat、开始录音）。</p>
        </div>
      </div>
    </div>
  );
}
