/**
 * SettingsPanel - AI 模式与运行模式设置
 *
 * LLM Mode: Auto | Force Local | Force Cloud
 * TTS Mode: Auto | Force Local | Force Cloud
 * Run Mode: Standalone | Client
 */

import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { Settings, Loader2, RefreshCw, Save, Key } from "lucide-react";
import { saveApiKey } from "../../lib/api";
import { cn } from "../../utils/cn";

/** 桌面精灵语音模式：三层架构 */
export type SpriteVoiceMode = "push_to_talk" | "wake_up" | "continuous";

export interface UserSettings {
  llm_provider_override?: string | null;
  stt_provider_override?: string | null;
  tts_provider_override?: string | null;
  run_mode_override?: string | null;
  custom_model_path?: string | null;
  /** true=本地流式直连后端，false=经 Dapr。默认 true */
  chat_stream_via_direct?: boolean | null;
  /** 精灵语音模式：push_to_talk | wake_up | continuous。默认 push_to_talk */
  sprite_voice_mode?: SpriteVoiceMode | string | null;
  /** 唤醒词/名字（模式 B）：说此词时激活助手。默认 Jachin */
  wake_word?: string | null;
  /** Qwen/通义千问 API Key（保存后持久化，无需每次配置） */
  qwen_api_key?: string | null;
}

export interface RuntimeConfig {
  llm_provider: string;
  tts_provider: string;
  stt_provider: string;
  run_mode: string;
}

const LLM_OPTIONS = [
  { value: null, label: "Auto (Default)" },
  { value: "local", label: "Force Local" },
  { value: "cloud", label: "Force Cloud" },
] as const;

const TTS_OPTIONS = [
  { value: null, label: "Auto (Default)" },
  { value: "local", label: "Force Local" },
  { value: "cloud", label: "Force Cloud" },
] as const;

const RUN_MODE_OPTIONS = [
  { value: "standalone", label: "Standalone" },
  { value: "client", label: "Client" },
] as const;

const CHAT_STREAM_OPTIONS = [
  { value: true, label: "本地流式（直连后端，推荐）" },
  { value: false, label: "远程经 Dapr" },
] as const;

const SPRITE_VOICE_MODE_OPTIONS = [
  { value: "push_to_talk", label: "A. 录音模式 (Push-to-Talk)" },
  { value: "wake_up", label: "B. 唤醒模式 (Wake-Up)" },
  { value: "continuous", label: "C. 识别模式 (Continuous)" },
] as const;

export function SettingsPanel() {
  const [config, setConfig] = useState<RuntimeConfig | null>(null);
  const [settings, setSettings] = useState<UserSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [restartHint, setRestartHint] = useState(false);
  const [apiKeyInput, setApiKeyInput] = useState("");
  const [apiKeySaving, setApiKeySaving] = useState(false);
  const [apiKeySaved, setApiKeySaved] = useState(false);

  const fetchData = async () => {
    setLoading(true);
    try {
      const [cfg, s] = await Promise.all([
        invoke<RuntimeConfig>("get_current_config"),
        invoke<UserSettings>("get_user_settings"),
      ]);
      setConfig(cfg);
      setSettings(s ?? {});
    } catch {
      setConfig(null);
      setSettings(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  useEffect(() => {
    const unlisten = listen<{ restart_required: boolean }>("settings-updated", (event) => {
      if (event.payload?.restart_required) {
        setRestartHint(true);
      }
      void fetchData();
    });
    return () => {
      unlisten.then((fn) => fn());
    };
  }, []);

  const handleLlmChange = async (value: string | null) => {
    if (saving || !settings) return;
    setSaving(true);
    try {
      await invoke("update_user_settings", {
        patch: { ...settings, llm_provider_override: value || null },
      });
    } finally {
      setSaving(false);
    }
  };

  const handleTtsChange = async (value: string | null) => {
    if (saving || !settings) return;
    setSaving(true);
    try {
      await invoke("update_user_settings", {
        patch: { ...settings, tts_provider_override: value || null },
      });
    } finally {
      setSaving(false);
    }
  };

  const handleRunModeChange = async (value: string) => {
    if (saving || !settings) return;
    setSaving(true);
    try {
      await invoke("update_user_settings", {
        patch: { ...settings, run_mode_override: value },
      });
    } finally {
      setSaving(false);
    }
  };

  const handleChatStreamChange = async (value: boolean) => {
    if (saving || !settings) return;
    setSaving(true);
    try {
      await invoke("update_user_settings", {
        patch: { ...settings, chat_stream_via_direct: value },
      });
    } finally {
      setSaving(false);
    }
  };

  const handleSpriteVoiceModeChange = async (value: string) => {
    if (saving || !settings) return;
    setSaving(true);
    try {
      await invoke("update_user_settings", {
        patch: { ...settings, sprite_voice_mode: value as UserSettings["sprite_voice_mode"] },
      });
    } finally {
      setSaving(false);
    }
  };

  const handleSaveApiKey = async () => {
    if (apiKeySaving || !settings) return;
    setApiKeySaving(true);
    setApiKeySaved(false);
    try {
      const keyToSave = apiKeyInput.trim() || null;
      await invoke("update_user_settings", {
        patch: { ...settings, qwen_api_key: keyToSave },
      });
      const res = await saveApiKey(keyToSave);
      setApiKeySaved(res.ok);
      if (res.ok) {
        setApiKeyInput("");
      }
    } catch {
      setApiKeySaved(false);
    } finally {
      setApiKeySaving(false);
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
          className="font-sci-fi text-2xl font-bold text-transparent bg-clip-text bg-gradient-to-r from-rose-400 to-rose-600"
          style={{ fontFamily: "Orbitron, sans-serif" }}
        >
          Settings
        </h1>
        <p className="text-slate-500 text-sm mt-0.5">AI 模式与运行模式</p>
      </header>

      {restartHint && (
        <motion.div
          initial={{ opacity: 0, y: -8 }}
          animate={{ opacity: 1, y: 0 }}
          className="mb-4 px-4 py-3 rounded-lg bg-amber-500/20 border border-amber-500/40 text-amber-200 text-sm"
        >
          需要重启应用以应用更改
        </motion.div>
      )}

      <div className="flex-1 space-y-6">
        <motion.section
          className="glass-panel rounded-xl overflow-hidden"
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3 }}
        >
          <div className="px-4 py-3 border-b border-white/10 flex items-center gap-2">
            <Settings className="w-4 h-4 text-rose-400/80" />
            <span className="font-mono text-xs uppercase tracking-wider text-slate-400">
              当前生效
            </span>
            <button
              onClick={() => void fetchData()}
              className="ml-auto p-1 rounded text-slate-400 hover:text-rose-400 transition-colors"
              title="刷新"
            >
              <RefreshCw className="w-4 h-4" />
            </button>
          </div>
          <div className="p-4 space-y-2 text-sm font-mono text-slate-300">
            {config && (
              <>
                <div>LLM: {config.llm_provider}</div>
                <div>TTS: {config.tts_provider}</div>
                <div>STT: {config.stt_provider}</div>
                <div>Run Mode: {config.run_mode}</div>
              </>
            )}
          </div>
        </motion.section>

        <motion.section
          className="glass-panel rounded-xl overflow-hidden"
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3, delay: 0.05 }}
        >
          <div className="px-4 py-3 border-b border-white/10">
            <span className="font-mono text-xs uppercase tracking-wider text-slate-400">
              用户覆盖
            </span>
          </div>
          <div className="p-4 space-y-4">
            <div>
              <label className="block text-sm font-mono text-slate-400 mb-2">
                <Key className="w-3.5 h-3.5 inline mr-1.5" />
                Qwen API Key（通义千问）
              </label>
              <div className="flex gap-2">
                <input
                  type="password"
                  value={apiKeyInput}
                  onChange={(e) => setApiKeyInput(e.target.value)}
                  placeholder={settings?.qwen_api_key ? "已保存，输入新值可更新" : "sk-xxx（留空则使用 .env 配置）"}
                  disabled={apiKeySaving}
                  className={cn(
                    "flex-1 px-3 py-2 rounded border bg-white/5 text-slate-200 text-sm font-mono",
                    "border-white/10 focus:border-rose-500/50 focus:ring-1 focus:ring-rose-500/30",
                    "placeholder:text-slate-500 disabled:opacity-50"
                  )}
                />
                <button
                  onClick={handleSaveApiKey}
                  disabled={apiKeySaving}
                  className={cn(
                    "px-4 py-2 rounded font-mono text-sm flex items-center gap-2",
                    "bg-rose-500/20 hover:bg-rose-500/30 border border-rose-500/40 text-rose-200",
                    "disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                  )}
                >
                  {apiKeySaving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                  保存
                </button>
              </div>
              {apiKeySaved && (
                <p className="text-xs text-emerald-400 mt-1">已保存，后续无需重复配置</p>
              )}
              <p className="text-xs text-slate-500 mt-1">保存后立即生效；若未生效请重启后端</p>
            </div>

            <div>
              <label className="block text-sm font-mono text-slate-400 mb-2">LLM Mode</label>
              <select
                value={settings?.llm_provider_override ?? ""}
                onChange={(e) =>
                  handleLlmChange(e.target.value || null)
                }
                disabled={saving}
                className={cn(
                  "w-full px-3 py-2 rounded border bg-white/5 text-slate-200 text-sm font-mono",
                  "border-white/10 focus:border-rose-500/50 focus:ring-1 focus:ring-rose-500/30",
                  "disabled:opacity-50"
                )}
              >
                {LLM_OPTIONS.map((o) => (
                  <option key={o.label} value={o.value ?? ""}>
                    {o.label}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="block text-sm font-mono text-slate-400 mb-2">TTS Mode</label>
              <select
                value={settings?.tts_provider_override ?? ""}
                onChange={(e) =>
                  handleTtsChange(e.target.value || null)
                }
                disabled={saving}
                className={cn(
                  "w-full px-3 py-2 rounded border bg-white/5 text-slate-200 text-sm font-mono",
                  "border-white/10 focus:border-rose-500/50 focus:ring-1 focus:ring-rose-500/30",
                  "disabled:opacity-50"
                )}
              >
                {TTS_OPTIONS.map((o) => (
                  <option key={o.label} value={o.value ?? ""}>
                    {o.label}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="block text-sm font-mono text-slate-400 mb-2">Run Mode</label>
              <select
                value={settings?.run_mode_override ?? "standalone"}
                onChange={(e) => handleRunModeChange(e.target.value)}
                disabled={saving}
                className={cn(
                  "w-full px-3 py-2 rounded border bg-white/5 text-slate-200 text-sm font-mono",
                  "border-white/10 focus:border-rose-500/50 focus:ring-1 focus:ring-rose-500/30",
                  "disabled:opacity-50"
                )}
              >
                {RUN_MODE_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="block text-sm font-mono text-slate-400 mb-2">Chat 流式</label>
              <select
                value={settings?.chat_stream_via_direct === false ? "false" : "true"}
                onChange={(e) => handleChatStreamChange(e.target.value === "true")}
                disabled={saving}
                className={cn(
                  "w-full px-3 py-2 rounded border bg-white/5 text-slate-200 text-sm font-mono",
                  "border-white/10 focus:border-rose-500/50 focus:ring-1 focus:ring-rose-500/30",
                  "disabled:opacity-50"
                )}
              >
                {CHAT_STREAM_OPTIONS.map((o) => (
                  <option key={String(o.value)} value={String(o.value)}>
                    {o.label}
                  </option>
                ))}
              </select>
              <p className="text-xs text-slate-500 mt-1">默认本地流式，避免 Dapr 缓冲导致无回复</p>
            </div>

            <div>
              <label className="block text-sm font-mono text-slate-400 mb-2">桌面精灵语音模式</label>
              <select
                value={settings?.sprite_voice_mode ?? "push_to_talk"}
                onChange={(e) => handleSpriteVoiceModeChange(e.target.value)}
                disabled={saving}
                className={cn(
                  "w-full px-3 py-2 rounded border bg-white/5 text-slate-200 text-sm font-mono",
                  "border-white/10 focus:border-rose-500/50 focus:ring-1 focus:ring-rose-500/30",
                  "disabled:opacity-50"
                )}
              >
                {SPRITE_VOICE_MODE_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
              <div className="text-xs text-slate-500 mt-2 space-y-1">
                <p>A. 录音：低资源、极低误触，隐私/嘈杂/长文本</p>
                <p>B. 唤醒：中资源(KWS)、需唤醒词，远场/双手占用</p>
                <p>C. 连续：高资源(VAD+STT)、易误触，沉浸闲聊</p>
              </div>
            </div>
          </div>
        </motion.section>
      </div>
    </div>
  );
}
