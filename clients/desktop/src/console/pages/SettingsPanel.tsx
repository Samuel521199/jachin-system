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
import {
  Settings,
  Loader2,
  RefreshCw,
  Save,
  Key,
  Shield,
  ShieldOff,
  FolderOpen,
  Plus,
  Trash2,
} from "lucide-react";
import { fetchNativeFsPolicy, saveApiKey, saveNativeFsPolicy, type NativeFsPolicyPayload } from "../../lib/api";
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

/** L3 不可用时用于展示的内置读取黑名单说明（与 fs_path_blacklist 简述对齐） */
const READ_BLACKLIST_BUILTIN_FALLBACK: string[] = [
  "密钥与云凭证目录：.ssh、.aws、.kube、.gnupg",
  "环境变量文件：路径段含 .env 或 credentials",
  "Windows 系统目录（含 System32、SysWOW64、WindowsApps 及 C:\\Windows\\…）",
  "SAM/SECURITY 注册表配置单元",
  "路径段名为 etc（含 C:\\etc、/etc/…）",
  "Linux /boot（根下 boot 段）及盘符根下 Boot",
  "/var/log 及 /etc/shadow、/etc/passwd",
  "Chromium 系浏览器用户数据中的 Cookies / Login Data",
];

export function SettingsPanel() {
  const [config, setConfig] = useState<RuntimeConfig | null>(null);
  const [settings, setSettings] = useState<UserSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [restartHint, setRestartHint] = useState(false);
  const [apiKeyInput, setApiKeyInput] = useState("");
  const [apiKeySaving, setApiKeySaving] = useState(false);
  const [apiKeySaved, setApiKeySaved] = useState(false);

  const [fsPolicy, setFsPolicy] = useState<NativeFsPolicyPayload | null>(null);
  const [fsPolicyLoading, setFsPolicyLoading] = useState(false);
  const [fsPolicyError, setFsPolicyError] = useState<string | null>(null);
  const [customWriteRoots, setCustomWriteRoots] = useState<string[]>([]);
  const [customReadBlacklist, setCustomReadBlacklist] = useState<string[]>([]);
  const [writePathDraft, setWritePathDraft] = useState("");
  const [readPathDraft, setReadPathDraft] = useState("");
  const [fsPolicySaving, setFsPolicySaving] = useState(false);
  const [fsPolicySavedHint, setFsPolicySavedHint] = useState(false);

  const loadFsPolicy = async () => {
    setFsPolicyLoading(true);
    setFsPolicyError(null);
    try {
      const p = await fetchNativeFsPolicy();
      if (p?.ok === false && p.error) {
        setFsPolicyError(p.error);
        return;
      }
      setFsPolicy(p);
      setCustomWriteRoots([...(p.custom_write_roots ?? [])]);
      setCustomReadBlacklist([...(p.custom_read_blacklist_roots ?? [])]);
    } catch (e) {
      setFsPolicy(null);
      setFsPolicyError(e instanceof Error ? e.message : String(e));
    } finally {
      setFsPolicyLoading(false);
    }
  };

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
    void loadFsPolicy();
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

  const handleSaveFsPolicy = async () => {
    if (fsPolicySaving) return;
    setFsPolicySaving(true);
    setFsPolicySavedHint(false);
    try {
      const res = await saveNativeFsPolicy({
        write_allowlist_extra: customWriteRoots,
        read_blacklist_extra: customReadBlacklist,
      });
      if (res.ok === false || res.error) {
        setFsPolicyError(res.error ?? "保存失败");
        return;
      }
      setFsPolicySavedHint(true);
      await loadFsPolicy();
    } catch (e) {
      setFsPolicyError(e instanceof Error ? e.message : String(e));
    } finally {
      setFsPolicySaving(false);
    }
  };

  const addCustomWriteRoot = () => {
    const t = writePathDraft.trim();
    if (!t) return;
    setCustomWriteRoots((prev) => (prev.includes(t) ? prev : [...prev, t]));
    setWritePathDraft("");
  };

  const addCustomReadBlacklist = () => {
    const t = readPathDraft.trim();
    if (!t) return;
    setCustomReadBlacklist((prev) => (prev.includes(t) ? prev : [...prev, t]));
    setReadPathDraft("");
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
          transition={{ duration: 0.3, delay: 0.04 }}
        >
          <div className="px-4 py-3 border-b border-white/10 flex items-center gap-2">
            <FolderOpen className="w-4 h-4 text-cyan-400/80" />
            <span className="font-mono text-xs uppercase tracking-wider text-slate-400">
              Native 文件系统策略
            </span>
            <button
              type="button"
              onClick={() => void loadFsPolicy()}
              disabled={fsPolicyLoading}
              className="ml-auto p-1 rounded text-slate-400 hover:text-cyan-400 transition-colors disabled:opacity-40"
              title="从 L3 刷新"
            >
              {fsPolicyLoading ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <RefreshCw className="w-4 h-4" />
              )}
            </button>
          </div>
          <div className="p-4 space-y-6 text-sm">
            {fsPolicy?.policy_file && (
              <p className="text-xs text-slate-500 font-mono break-all">
                配置：{fsPolicy.policy_file}
              </p>
            )}
            {fsPolicyError && (
              <p className="text-xs text-amber-400/90 leading-relaxed">{fsPolicyError}</p>
            )}

            <div>
              <div className="flex items-center gap-2 mb-1.5">
                <Shield className="w-4 h-4 text-emerald-400/90" />
                <span className="font-mono text-slate-300">写入白名单（内置）</span>
              </div>
              <p className="text-xs text-slate-500 mb-2">
                系统内置允许 Native 写入的根目录（如 workspace、client_volumes、HR 数据、用户文档目录等），不可在此页移除。
              </p>
              <ul className="text-xs font-mono text-slate-400 space-y-1 max-h-36 overflow-y-auto rounded border border-white/10 p-2 bg-black/20">
                {(fsPolicy?.builtin_write_roots ?? []).length === 0 ? (
                  <li className="text-slate-500">
                    {fsPolicyError
                      ? "（需连接 L3 以显示解析后的内置绝对路径）"
                      : "（内置绝对路径由 L3 在线时填充；下方「额外」列表已可从本机策略文件加载）"}
                  </li>
                ) : (
                  (fsPolicy?.builtin_write_roots ?? []).map((p) => <li key={p}>{p}</li>)
                )}
              </ul>
            </div>

            <div>
              <div className="flex items-center gap-2 mb-1.5">
                <Shield className="w-4 h-4 text-cyan-400/90" />
                <span className="font-mono text-slate-300">写入白名单（额外）</span>
              </div>
              <p className="text-xs text-slate-500 mb-2">
                在此追加允许写入的目录根；保存后经后端校验并合并进白名单，影响 core:fs_write 等工具。
              </p>
              <ul className="text-xs font-mono text-slate-300 space-y-1 mb-2">
                {customWriteRoots.map((p) => (
                  <li
                    key={p}
                    className="flex items-center gap-2 rounded border border-white/10 px-2 py-1 bg-white/5"
                  >
                    <span className="flex-1 truncate" title={p}>
                      {p}
                    </span>
                    <button
                      type="button"
                      onClick={() => setCustomWriteRoots((prev) => prev.filter((x) => x !== p))}
                      className="p-1 rounded text-slate-500 hover:text-rose-400"
                      title="移除此项"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </li>
                ))}
                {customWriteRoots.length === 0 && (
                  <li className="text-slate-500 text-xs">暂无额外路径</li>
                )}
              </ul>
              <div className="flex gap-2">
                <input
                  type="text"
                  value={writePathDraft}
                  onChange={(e) => setWritePathDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      addCustomWriteRoot();
                    }
                  }}
                  placeholder="绝对路径，如 D:\\Projects\\my-data"
                  className={cn(
                    "flex-1 px-3 py-2 rounded border bg-white/5 text-slate-200 text-xs font-mono",
                    "border-white/10 focus:border-cyan-500/50 focus:ring-1 focus:ring-cyan-500/30",
                    "placeholder:text-slate-600"
                  )}
                />
                <button
                  type="button"
                  onClick={addCustomWriteRoot}
                  className={cn(
                    "px-3 py-2 rounded font-mono text-xs flex items-center gap-1.5 shrink-0",
                    "bg-cyan-500/15 hover:bg-cyan-500/25 border border-cyan-500/35 text-cyan-200"
                  )}
                >
                  <Plus className="w-3.5 h-3.5" />
                  添加
                </button>
              </div>
            </div>

            <div>
              <div className="flex items-center gap-2 mb-1.5">
                <ShieldOff className="w-4 h-4 text-rose-400/80" />
                <span className="font-mono text-slate-300">读取黑名单（内置）</span>
              </div>
              <p className="text-xs text-slate-500 mb-2">
                下列类型的路径禁止通过 Native 读取（底线规则，不可关闭）：
              </p>
              <ul className="text-xs text-slate-400 space-y-1 list-disc list-inside leading-relaxed">
                {(fsPolicy?.builtin_read_blacklist_lines ?? READ_BLACKLIST_BUILTIN_FALLBACK).map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            </div>

            <div>
              <div className="flex items-center gap-2 mb-1.5">
                <ShieldOff className="w-4 h-4 text-orange-400/90" />
                <span className="font-mono text-slate-300">读取黑名单（额外）</span>
              </div>
              <p className="text-xs text-slate-500 mb-2">
                在此追加禁止读取的目录根：位于其下的任意路径均会被 core:fs_read 拒绝。
              </p>
              <ul className="text-xs font-mono text-slate-300 space-y-1 mb-2">
                {customReadBlacklist.map((p) => (
                  <li
                    key={p}
                    className="flex items-center gap-2 rounded border border-white/10 px-2 py-1 bg-white/5"
                  >
                    <span className="flex-1 truncate" title={p}>
                      {p}
                    </span>
                    <button
                      type="button"
                      onClick={() => setCustomReadBlacklist((prev) => prev.filter((x) => x !== p))}
                      className="p-1 rounded text-slate-500 hover:text-rose-400"
                      title="移除此项"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </li>
                ))}
                {customReadBlacklist.length === 0 && (
                  <li className="text-slate-500 text-xs">暂无额外禁止路径</li>
                )}
              </ul>
              <div className="flex gap-2">
                <input
                  type="text"
                  value={readPathDraft}
                  onChange={(e) => setReadPathDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      addCustomReadBlacklist();
                    }
                  }}
                  placeholder="绝对路径，如 D:\\Secrets"
                  className={cn(
                    "flex-1 px-3 py-2 rounded border bg-white/5 text-slate-200 text-xs font-mono",
                    "border-white/10 focus:border-orange-500/50 focus:ring-1 focus:ring-orange-500/30",
                    "placeholder:text-slate-600"
                  )}
                />
                <button
                  type="button"
                  onClick={addCustomReadBlacklist}
                  className={cn(
                    "px-3 py-2 rounded font-mono text-xs flex items-center gap-1.5 shrink-0",
                    "bg-orange-500/15 hover:bg-orange-500/25 border border-orange-500/35 text-orange-200"
                  )}
                >
                  <Plus className="w-3.5 h-3.5" />
                  添加
                </button>
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-3 pt-1">
              <button
                type="button"
                onClick={() => void handleSaveFsPolicy()}
                disabled={fsPolicySaving}
                className={cn(
                  "px-4 py-2 rounded font-mono text-sm flex items-center gap-2",
                  "bg-rose-500/20 hover:bg-rose-500/30 border border-rose-500/40 text-rose-200",
                  "disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                )}
              >
                {fsPolicySaving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                保存路径策略
              </button>
              {fsPolicySavedHint && (
                <span className="text-xs text-emerald-400">已保存，L3 将重新加载策略文件</span>
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
