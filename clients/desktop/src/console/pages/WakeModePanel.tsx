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
import { DEFAULT_WAKE_WORD, validateWakeWord } from "../../voice/wakeWordValidation";
import { previewWakeAckWav } from "../../voice/voiceNativeBridge";

export interface UserSettingsWake {
  wake_word?: string | null;
  wake_ack_mode?: string | null;
  wake_ack_pool?: string[] | null;
  wake_ack_phrase?: string | null;
  wake_ack_show_in_hud?: boolean | null;
  speaker_verification_enabled?: boolean | null;
  speaker_verification_strict?: boolean | null;
  speaker_owner_track_enabled?: boolean | null;
}

const WAKE_ACK_PRESETS: { id: string; label: string }[] = [
  { id: "im_here", label: "我在" },
  { id: "yes", label: "嗯" },
  { id: "how_can_i_help", label: "有什么可以帮你" },
  { id: "please_say", label: "请说" },
];

const ENROLL_SAMPLE_PROMPTS = [
  "样本 1（唤醒句）：请用平常语气说你的唤醒词",
  "样本 2（普通句）：请说一句日常指令，例如“今天天气怎么样”",
  "样本 3（普通句）：请再说一句不同语气的短句",
];

export function WakeModePanel() {
  const [wakeWord, setWakeWord] = useState(DEFAULT_WAKE_WORD);
  const [savedWakeWord, setSavedWakeWord] = useState<string | null>(null);
  const [isListening, setIsListening] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [lastWakePayload, setLastWakePayload] = useState<{ wake_word?: string } | null>(null);
  const [wakeWordError, setWakeWordError] = useState<string | null>(null);
  const [isVoiceCaptureRunning, setIsVoiceCaptureRunning] = useState(false);
  const [wakeAckMode, setWakeAckMode] = useState("both");
  const [wakeAckPhrase, setWakeAckPhrase] = useState("");
  const [wakeAckPreviewId, setWakeAckPreviewId] = useState("im_here");
  const [speakerVerificationEnabled, setSpeakerVerificationEnabled] = useState(true);
  const [speakerVerificationStrict, setSpeakerVerificationStrict] = useState(false);
  const [speakerOwnerTrackEnabled, setSpeakerOwnerTrackEnabled] = useState(true);
  const [enrollSamples, setEnrollSamples] = useState<(string | null)[]>([null, null, null]);
  const [enrollStep, setEnrollStep] = useState(0);
  const [enrollRecording, setEnrollRecording] = useState(false);
  const [enrollSaving, setEnrollSaving] = useState(false);
  const [enrollMessage, setEnrollMessage] = useState<string | null>(null);

  const loadSettings = async () => {
    setLoading(true);
    try {
      const settings = await invoke<UserSettingsWake>("get_user_settings");
      const word = (settings?.wake_word?.trim() || DEFAULT_WAKE_WORD) as string;
      setWakeWord(word);
      setSavedWakeWord(word);
      setWakeAckMode(settings?.wake_ack_mode?.trim() || "both");
      setWakeAckPhrase(settings?.wake_ack_phrase?.trim() || "");
      setSpeakerVerificationEnabled(settings?.speaker_verification_enabled ?? true);
      setSpeakerVerificationStrict(settings?.speaker_verification_strict ?? false);
      setSpeakerOwnerTrackEnabled(settings?.speaker_owner_track_enabled ?? true);
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
    const v = validateWakeWord(wakeWord);
    if (!v.ok) {
      setWakeWordError(v.message);
      return;
    }
    setWakeWordError(null);
    const word = v.value;
    setSaving(true);
    try {
      const settings = await invoke<UserSettingsWake>("get_user_settings");
      await invoke("update_user_settings", {
        patch: {
          ...settings,
          wake_word: word,
          wake_ack_mode: wakeAckMode,
          wake_ack_phrase: wakeAckPhrase.trim() || null,
          wake_ack_pool: ["im_here", "yes", "how_can_i_help"],
          speaker_verification_enabled: speakerVerificationEnabled,
          speaker_verification_strict: speakerVerificationStrict,
          speaker_owner_track_enabled: speakerOwnerTrackEnabled,
        },
      });
      setSavedWakeWord(word);
      setWakeWord(word);
      if (isListening) {
        await invoke("stt_stop_wake_listener");
        await invoke("stt_start_wake_listener", { wake_word: word });
      }
    } finally {
      setSaving(false);
    }
  };

  const handleStartListening = async () => {
    const v = validateWakeWord(wakeWord);
    if (!v.ok) {
      setWakeWordError(v.message);
      return;
    }
    setWakeWordError(null);
    const word = v.value;
    try {
      await invoke("stt_start_wake_listener", { wake_word: word });
      setIsListening(true);
      setSavedWakeWord(word);
    } catch (e) {
      console.error("Start wake listener failed:", e);
      setWakeWordError(String(e));
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

  const handleStartEnrollSample = async () => {
    setEnrollMessage(null);
    try {
      await invoke("start_ptt_capture");
      setEnrollRecording(true);
    } catch (e) {
      setEnrollMessage(`开始录音失败：${String(e)}`);
    }
  };

  const handleStopEnrollSample = async () => {
    try {
      const payload = await invoke<{ wav_base64: string }>("stop_ptt_capture");
      const b64 = payload?.wav_base64?.trim();
      if (!b64) {
        setEnrollMessage("录音为空，请重录该样本。");
        setEnrollRecording(false);
        return;
      }
      setEnrollSamples((prev) => {
        const next = [...prev];
        next[enrollStep] = b64;
        return next;
      });
      setEnrollRecording(false);
      setEnrollMessage(`已保存样本 ${enrollStep + 1}。`);
      setEnrollStep((s) => Math.min(ENROLL_SAMPLE_PROMPTS.length - 1, s + 1));
    } catch (e) {
      setEnrollRecording(false);
      setEnrollMessage(`结束录音失败：${String(e)}`);
    }
  };

  const handleReRecordCurrent = () => {
    setEnrollMessage(null);
    setEnrollSamples((prev) => {
      const next = [...prev];
      next[enrollStep] = null;
      return next;
    });
  };

  const handleEnrollOwner = async () => {
    const ready = enrollSamples.filter((s): s is string => Boolean(s && s.trim()));
    if (ready.length < 3) {
      setEnrollMessage("请先录满 3 段样本再生成主人音色。");
      return;
    }
    setEnrollSaving(true);
    setEnrollMessage(null);
    try {
      const result = await invoke<{
        ok: boolean;
        path: string;
        sample_count: number;
        embedding_dim: number;
      }>("enroll_owner_voiceprint", {
        req: { sample_wavs_base64: ready },
      });
      if (result?.ok) {
        setEnrollMessage(
          `认主成功：已写入 ${result.path}（${result.sample_count} 段，${result.embedding_dim} 维）。请保存设置并重启唤醒监听。`
        );
      } else {
        setEnrollMessage("认主失败，请重试。");
      }
    } catch (e) {
      setEnrollMessage(`认主失败：${String(e)}`);
    } finally {
      setEnrollSaving(false);
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
          transition={{ duration: 0.3, delay: 0.04 }}
        >
          <div className="px-4 py-3 border-b border-white/10 flex items-center gap-2">
            <span className="font-mono text-xs uppercase tracking-wider text-slate-400">
              声纹识主（Speaker Verification）
            </span>
          </div>
          <div className="p-4 space-y-3">
            <p className="text-xs text-slate-500">
              唤醒时先验主人声纹；录指令阶段可按时间窗过滤旁人插话（需本机已有 owner_voiceprint.json）。
            </p>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => setSpeakerVerificationEnabled((v) => !v)}
                className={cn(
                  "px-3 py-1.5 rounded-lg border text-xs font-mono",
                  speakerVerificationEnabled
                    ? "border-cyan-500/50 bg-cyan-500/15 text-cyan-300"
                    : "border-white/10 text-slate-400 hover:bg-white/5",
                )}
              >
                声纹门：{speakerVerificationEnabled ? "开启" : "关闭"}
              </button>
              <button
                type="button"
                onClick={() => setSpeakerOwnerTrackEnabled((v) => !v)}
                className={cn(
                  "px-3 py-1.5 rounded-lg border text-xs font-mono",
                  speakerOwnerTrackEnabled
                    ? "border-cyan-500/50 bg-cyan-500/15 text-cyan-300"
                    : "border-white/10 text-slate-400 hover:bg-white/5",
                )}
              >
                主人轨提取：{speakerOwnerTrackEnabled ? "开启" : "关闭"}
              </button>
              <button
                type="button"
                onClick={() => setSpeakerVerificationStrict((v) => !v)}
                className={cn(
                  "px-3 py-1.5 rounded-lg border text-xs font-mono",
                  speakerVerificationStrict
                    ? "border-rose-500/50 bg-rose-500/15 text-rose-300"
                    : "border-white/10 text-slate-400 hover:bg-white/5",
                )}
              >
                严格模式：{speakerVerificationStrict ? "开启" : "关闭"}
              </button>
            </div>
            <p className="text-[10px] text-slate-500">
              严格模式下，声纹服务不可用或 owner profile 缺失时会拒绝录入语音。
            </p>
          </div>
        </motion.section>

        <motion.section
          className="glass-panel rounded-xl overflow-hidden border border-cyan-500/20"
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3, delay: 0.045 }}
        >
          <div className="px-4 py-3 border-b border-white/10 flex items-center gap-2 bg-cyan-500/5">
            <span className="font-mono text-xs uppercase tracking-wider text-cyan-300/90">
              一键认主（录制音色）
            </span>
          </div>
          <div className="p-4 space-y-3">
            <p className="text-xs text-slate-500">
              依次录 3 段样本后自动生成 <code className="text-cyan-400">owner_voiceprint.json</code>。
              录制时请在安静环境下，麦克风距离嘴部保持一致。
            </p>
            <div className="text-xs text-slate-300">
              当前步骤：{enrollStep + 1}/3 · {ENROLL_SAMPLE_PROMPTS[enrollStep]}
            </div>
            <div className="flex flex-wrap gap-2 text-xs">
              {ENROLL_SAMPLE_PROMPTS.map((_, idx) => (
                <span
                  key={idx}
                  className={cn(
                    "px-2 py-1 rounded border",
                    enrollSamples[idx]
                      ? "border-emerald-500/40 bg-emerald-500/15 text-emerald-300"
                      : idx === enrollStep
                      ? "border-cyan-500/40 bg-cyan-500/15 text-cyan-300"
                      : "border-white/10 text-slate-400"
                  )}
                >
                  样本 {idx + 1}{enrollSamples[idx] ? " ✓" : ""}
                </span>
              ))}
            </div>
            <div className="flex flex-wrap gap-2">
              {!enrollRecording ? (
                <button
                  type="button"
                  onClick={() => void handleStartEnrollSample()}
                  className="px-3 py-2 rounded border border-cyan-500/40 bg-cyan-500/15 text-cyan-300 text-xs hover:bg-cyan-500/25"
                >
                  开始录当前样本
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => void handleStopEnrollSample()}
                  className="px-3 py-2 rounded border border-rose-500/40 bg-rose-500/15 text-rose-300 text-xs hover:bg-rose-500/25"
                >
                  结束并保存当前样本
                </button>
              )}
              <button
                type="button"
                onClick={handleReRecordCurrent}
                disabled={enrollRecording}
                className="px-3 py-2 rounded border border-white/15 text-slate-300 text-xs hover:bg-white/10 disabled:opacity-50"
              >
                重录当前样本
              </button>
              <button
                type="button"
                onClick={() => void handleEnrollOwner()}
                disabled={enrollRecording || enrollSaving}
                className="px-3 py-2 rounded border border-emerald-500/40 bg-emerald-500/15 text-emerald-300 text-xs hover:bg-emerald-500/25 disabled:opacity-50"
              >
                {enrollSaving ? "生成中..." : "生成主人音色"}
              </button>
            </div>
            {enrollMessage && <p className="text-xs text-slate-300">{enrollMessage}</p>}
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
              说出下面设置的词即可激活陪伴语音（STT 辅助 KWS，支持自定义）。需{" "}
              <code className="text-cyan-400">npm run tauri:dev:ambient</code>、silero_vad.onnx 与 JVS。
            </p>
            {wakeWordError && (
              <p className="text-xs text-rose-400">{wakeWordError}</p>
            )}
            {isListening && savedWakeWord && (
              <p className="text-xs text-emerald-400/90">当前监听：「{savedWakeWord}」</p>
            )}
            <div className="flex gap-2">
              <input
                type="text"
                value={wakeWord}
                onChange={(e) => {
                  setWakeWord(e.target.value);
                  setWakeWordError(null);
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
          transition={{ duration: 0.3, delay: 0.08 }}
        >
          <div className="px-4 py-3 border-b border-white/10 flex items-center gap-2">
            <span className="font-mono text-xs uppercase tracking-wider text-slate-400">
              唤醒确认（Verbal ACK）
            </span>
          </div>
          <div className="p-4 space-y-3">
            <p className="text-xs text-slate-500">
              唤醒后简短答应一声（预渲染本地 WAV，不走 L3）。随时开口或按 Ctrl+Space 可打断。
            </p>
            <div className="flex flex-wrap gap-2">
              {(["earcon_only", "verbal", "both"] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => setWakeAckMode(m)}
                  className={cn(
                    "px-3 py-1.5 rounded-lg border text-xs font-mono",
                    wakeAckMode === m
                      ? "border-cyan-500/50 bg-cyan-500/15 text-cyan-300"
                      : "border-white/10 text-slate-400 hover:bg-white/5",
                  )}
                >
                  {m === "earcon_only" ? "仅滴声" : m === "verbal" ? "仅口头" : "滴声+口头"}
                </button>
              ))}
            </div>
            <input
              type="text"
              value={wakeAckPhrase}
              onChange={(e) => setWakeAckPhrase(e.target.value)}
              placeholder="自定义确认语（留空则随机：我在/嗯/有什么可以帮你）"
              className={cn(
                "w-full px-3 py-2 rounded border bg-white/5 text-slate-200 text-sm",
                "border-white/10 focus:border-cyan-500/50",
              )}
            />
            <div className="flex flex-wrap items-center gap-2">
              <select
                value={wakeAckPreviewId}
                onChange={(e) => setWakeAckPreviewId(e.target.value)}
                className="px-2 py-1.5 rounded border border-white/10 bg-white/5 text-slate-300 text-xs"
              >
                {WAKE_ACK_PRESETS.map((p) => (
                  <option key={p.id} value={p.id}>{p.label}</option>
                ))}
              </select>
              <button
                type="button"
                onClick={() => void previewWakeAckWav(wakeAckPreviewId).catch(console.error)}
                className="px-3 py-1.5 rounded border border-white/20 text-slate-300 text-xs hover:bg-white/10"
              >
                试听
              </button>
              <button
                type="button"
                onClick={() => void handleSaveWakeWord()}
                className="px-3 py-1.5 rounded border border-cyan-500/30 text-cyan-400 text-xs hover:bg-cyan-500/10"
              >
                保存确认设置
              </button>
            </div>
            <p className="text-[10px] text-slate-500">
              首次使用请运行 <code className="text-cyan-400">python scripts/gen_wake_ack_wavs.py</code> 生成 WAV。
            </p>
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
          <p>· KWS 为 STT 辅助方案（约 2s 轮询 1.5s 音频窗），需 JVS 在线；后续可换 Porcupine 低功耗门卫。</p>
          <p>· 唤醒后 Rust 播放 Earcon，VAD 截断指令后注入陪伴链路（与语音模拟脚本同路径）。</p>
          <p>· 「模拟唤醒」在监听已开时直接进入 VAD 采集段，否则仅发 WAKE_UP 事件。</p>
        </div>
      </div>
    </div>
  );
}
