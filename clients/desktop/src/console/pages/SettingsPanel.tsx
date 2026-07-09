/**
 * SettingsPanel - AI 模式与运行模式设置
 *
 * LLM Mode: Auto | Force Local | Force Cloud
 * TTS Mode: Auto | Force Local | Force Cloud
 * Run Mode: Standalone | Client
 */

import { useState, useEffect, type ReactNode } from "react";
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
import { LarkLongConnectionSettings } from "./LarkLongConnectionSettings";

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
  /** Windows 文件高危操作是否免确认。默认 false */
  os_file_dangerous_without_confirm?: boolean | null;
  /** 飞书/Lark 多维表写入是否免确认。默认 false */
  lark_bitable_write_without_confirm?: boolean | null;
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
      if (value === "wake_up") {
        const word = settings.wake_word?.trim() || undefined;
        await invoke("stt_start_wake_listener", { wake_word: word }).catch(() => {});
      } else {
        await invoke("stt_stop_wake_listener").catch(() => {});
      }
    } finally {
      setSaving(false);
    }
  };

  const handleOsFileDangerousBypassChange = async (value: boolean) => {
    if (saving || !settings) return;
    setSaving(true);
    try {
      await invoke("update_user_settings", {
        patch: { ...settings, os_file_dangerous_without_confirm: value },
      });
    } finally {
      setSaving(false);
    }
  };

  const handleLarkBitableWriteBypassChange = async (value: boolean) => {
    if (saving || !settings) return;
    setSaving(true);
    try {
      await invoke("update_user_settings", {
        patch: { ...settings, lark_bitable_write_without_confirm: value },
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
      <div className="flex h-full items-center justify-center p-6">
        <div className="jarvis-panel flex items-center gap-3 rounded-[8px] border border-cyan-200/[0.08] bg-cyan-300/[0.018] px-5 py-4 text-cyan-100">
          <Loader2 className="h-5 w-5 animate-spin" />
          <span className="font-mono text-xs uppercase tracking-[0.16em]">Loading Settings</span>
        </div>
      </div>
    );
  }

  return (
    <div className="settings-console-page h-full overflow-auto p-5 sm:p-6">
      <div className="mx-auto flex max-w-[1180px] flex-col gap-5">
        <header className="jarvis-panel relative overflow-hidden rounded-[8px] border border-cyan-200/[0.08] bg-cyan-300/[0.018] p-5">
          <div className="jarvis-hero-grid opacity-[0.2]" aria-hidden />
          <div className="relative z-10 flex flex-col gap-5">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div className="flex items-center gap-4">
                <div className="jarvis-core-stage relative hidden h-20 w-20 flex-shrink-0 items-center justify-center sm:flex">
                  <svg className="jarvis-core-svg" viewBox="0 0 260 260" aria-hidden>
                    <circle className="jarvis-core-ring jarvis-core-ring-outer" cx="130" cy="130" r="108" />
                    <circle className="jarvis-core-ring jarvis-core-ring-mid" cx="130" cy="130" r="82" />
                    <circle className="jarvis-core-ring jarvis-core-ring-inner" cx="130" cy="130" r="58" />
                    <path className="jarvis-core-arc jarvis-core-arc-a" d="M130 22a108 108 0 0 1 99 65" />
                    <path className="jarvis-core-arc jarvis-core-arc-b" d="M51 204a108 108 0 0 1 0-148" />
                  </svg>
                  <Settings className="h-6 w-6 text-cyan-100 drop-shadow-[0_0_14px_rgba(125,211,252,0.55)]" />
                  <div className="jarvis-core-scan" aria-hidden />
                </div>
                <div>
                  <p className="mb-2 inline-flex rounded-full border border-cyan-200/[0.09] bg-cyan-300/[0.035] px-3 py-1 font-mono text-[10px] uppercase tracking-[0.18em] text-cyan-100/75">
                    System Preferences
                  </p>
                  <h1 className="text-2xl font-semibold text-slate-100 sm:text-3xl">Settings</h1>
                  <p className="mt-1 text-sm text-slate-400">AI 模式、运行通道与安全策略</p>
                </div>
              </div>
              <button
                onClick={() => void fetchData()}
                className="inline-flex h-10 items-center gap-2 rounded-[8px] border border-cyan-200/[0.12] bg-cyan-300/[0.045] px-4 text-sm text-cyan-50 transition hover:border-cyan-200/25 hover:bg-cyan-300/[0.075]"
              >
                <RefreshCw className="h-4 w-4" />
                刷新
              </button>
            </div>

            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <StatusTile label="LLM" value={config?.llm_provider ?? "unknown"} icon={<Settings className="h-4 w-4" />} />
              <StatusTile label="TTS" value={config?.tts_provider ?? "unknown"} icon={<Key className="h-4 w-4" />} />
              <StatusTile label="STT" value={config?.stt_provider ?? "unknown"} icon={<Shield className="h-4 w-4" />} />
              <StatusTile label="Run Mode" value={config?.run_mode ?? "unknown"} icon={<FolderOpen className="h-4 w-4" />} />
            </div>
          </div>
        </header>

      {restartHint && (
        <motion.div
          initial={{ opacity: 0, y: -8 }}
          animate={{ opacity: 1, y: 0 }}
            className="rounded-[8px] border border-amber-300/20 bg-amber-300/[0.06] px-4 py-3 text-sm text-amber-100"
        >
          需要重启应用以应用更改
        </motion.div>
      )}

        <div className="grid gap-5 xl:grid-cols-[minmax(0,0.95fr)_minmax(360px,0.65fr)]">
          <motion.section
            className="jarvis-panel rounded-[8px] border border-cyan-200/[0.08] bg-cyan-300/[0.018] p-4"
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.3 }}
          >
            <div className="mb-4 flex items-center gap-2">
              <ShieldOff className="h-4 w-4 text-amber-200/80" />
              <h2 className="font-mono text-[11px] uppercase tracking-[0.16em] text-cyan-100/80">Safety Gates</h2>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <ToggleCard
                title="高危文件免确认"
                summary={settings?.os_file_dangerous_without_confirm ? "已开放直接执行" : "保持二次确认"}
                checked={Boolean(settings?.os_file_dangerous_without_confirm)}
                disabled={saving || !settings}
                onChange={() => void handleOsFileDangerousBypassChange(!Boolean(settings?.os_file_dangerous_without_confirm))}
                tone="amber"
              />
              <ToggleCard
                title="飞书写入免确认"
                summary={settings?.lark_bitable_write_without_confirm ? "已开放直接写入" : "写入前先预览"}
                checked={Boolean(settings?.lark_bitable_write_without_confirm)}
                disabled={saving || !settings}
                onChange={() => void handleLarkBitableWriteBypassChange(!Boolean(settings?.lark_bitable_write_without_confirm))}
                tone="amber"
              />
            </div>
          </motion.section>

          <motion.section
            className="jarvis-panel rounded-[8px] border border-cyan-200/[0.08] bg-cyan-300/[0.018] p-4"
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.3, delay: 0.04 }}
          >
            <div className="mb-4 flex items-center gap-2">
              <Key className="h-4 w-4 text-cyan-100/80" />
              <h2 className="font-mono text-[11px] uppercase tracking-[0.16em] text-cyan-100/80">Qwen Access</h2>
            </div>
            <div>
              <div className="flex gap-2">
                <input
                  type="password"
                  value={apiKeyInput}
                  onChange={(e) => setApiKeyInput(e.target.value)}
                  placeholder={settings?.qwen_api_key ? "已保存，输入新值可更新" : "sk-xxx（留空则使用 .env 配置）"}
                  disabled={apiKeySaving}
                  className="h-10 min-w-0 flex-1 rounded-[8px] border border-cyan-200/[0.1] bg-slate-950/42 px-3 font-mono text-sm text-slate-100 outline-none placeholder:text-slate-500 focus:border-cyan-200/35 disabled:opacity-50"
                />
                <button
                  onClick={handleSaveApiKey}
                  disabled={apiKeySaving}
                  className="inline-flex h-10 items-center gap-2 rounded-[8px] border border-cyan-200/[0.13] bg-cyan-300/[0.055] px-3 text-sm text-cyan-50 transition hover:bg-cyan-300/[0.085] disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {apiKeySaving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
                  保存
                </button>
              </div>
              <p className="mt-2 text-xs text-slate-500">{apiKeySaved ? "已保存，后续无需重复配置" : "留空则继续使用环境配置"}</p>
            </div>
          </motion.section>
        </div>

        <motion.section
          className="jarvis-panel rounded-[8px] border border-cyan-200/[0.08] bg-cyan-300/[0.018] p-4"
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3, delay: 0.05 }}
        >
          <div className="mb-4 flex items-center gap-2">
            <Settings className="h-4 w-4 text-cyan-100/80" />
            <h2 className="font-mono text-[11px] uppercase tracking-[0.16em] text-cyan-100/80">Runtime Overrides</h2>
          </div>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
            <SelectControl
              label="LLM"
              value={settings?.llm_provider_override ?? ""}
              onChange={(value) => void handleLlmChange(value || null)}
              disabled={saving}
            >
              {LLM_OPTIONS.map((o) => (
                <option key={o.label} value={o.value ?? ""}>
                  {o.label}
                </option>
              ))}
            </SelectControl>

            <SelectControl
              label="TTS"
              value={settings?.tts_provider_override ?? ""}
              onChange={(value) => void handleTtsChange(value || null)}
              disabled={saving}
            >
              {TTS_OPTIONS.map((o) => (
                <option key={o.label} value={o.value ?? ""}>
                  {o.label}
                </option>
              ))}
            </SelectControl>

            <SelectControl
              label="Run"
              value={settings?.run_mode_override ?? "standalone"}
              onChange={(value) => void handleRunModeChange(value)}
              disabled={saving}
            >
              {RUN_MODE_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </SelectControl>

            <SelectControl
              label="Chat"
              value={settings?.chat_stream_via_direct === false ? "false" : "true"}
              onChange={(value) => void handleChatStreamChange(value === "true")}
              disabled={saving}
            >
              {CHAT_STREAM_OPTIONS.map((o) => (
                <option key={String(o.value)} value={String(o.value)}>
                  {o.label}
                </option>
              ))}
            </SelectControl>

            <SelectControl
              label="Voice"
              value={settings?.sprite_voice_mode ?? "push_to_talk"}
              onChange={(value) => void handleSpriteVoiceModeChange(value)}
              disabled={saving}
            >
              {SPRITE_VOICE_MODE_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </SelectControl>
          </div>
        </motion.section>

        <motion.section
          className="jarvis-panel rounded-[8px] border border-cyan-200/[0.08] bg-cyan-300/[0.018]"
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3, delay: 0.06 }}
        >
          <details>
            <summary className="flex cursor-pointer list-none items-center gap-3 px-4 py-4">
              <FolderOpen className="h-4 w-4 text-cyan-100/80" />
              <div className="min-w-0 flex-1">
                <div className="font-mono text-[11px] uppercase tracking-[0.16em] text-cyan-100/80">Advanced File Policy</div>
                <div className="mt-1 truncate text-xs text-slate-500">
                  {fsPolicy?.policy_file ? fsPolicy.policy_file : "Native 文件系统策略"}
                </div>
              </div>
              <button
                type="button"
                onClick={(e) => {
                  e.preventDefault();
                  void loadFsPolicy();
                }}
                disabled={fsPolicyLoading}
                className="inline-flex h-8 w-8 items-center justify-center rounded-[7px] border border-cyan-200/[0.08] text-slate-400 hover:border-cyan-200/22 hover:text-cyan-100 disabled:opacity-40"
                title="从 L3 刷新"
              >
                {fsPolicyLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
              </button>
            </summary>

            <div className="space-y-5 border-t border-cyan-200/[0.055] p-4 text-sm">
              {fsPolicyError && <p className="text-xs text-amber-300">{fsPolicyError}</p>}

              <div className="grid gap-4 xl:grid-cols-2">
                <PolicyBlock title="写入白名单" icon={<Shield className="h-4 w-4" />}>
                  <PathList
                    items={fsPolicy?.builtin_write_roots ?? []}
                    empty={fsPolicyError ? "需连接 L3 以显示内置绝对路径" : "内置路径由 L3 在线时填充"}
                  />
                  <EditablePathList
                    items={customWriteRoots}
                    draft={writePathDraft}
                    placeholder="追加允许写入目录"
                    onDraftChange={setWritePathDraft}
                    onAdd={addCustomWriteRoot}
                    onRemove={(path) => setCustomWriteRoots((prev) => prev.filter((x) => x !== path))}
                  />
                </PolicyBlock>

                <PolicyBlock title="读取黑名单" icon={<ShieldOff className="h-4 w-4" />}>
                  <PathList items={fsPolicy?.builtin_read_blacklist_lines ?? READ_BLACKLIST_BUILTIN_FALLBACK} empty="暂无内置规则" />
                  <EditablePathList
                    items={customReadBlacklist}
                    draft={readPathDraft}
                    placeholder="追加禁止读取目录"
                    onDraftChange={setReadPathDraft}
                    onAdd={addCustomReadBlacklist}
                    onRemove={(path) => setCustomReadBlacklist((prev) => prev.filter((x) => x !== path))}
                    tone="orange"
                  />
                </PolicyBlock>
              </div>

              <div className="flex flex-wrap items-center gap-3">
                <button
                  type="button"
                  onClick={() => void handleSaveFsPolicy()}
                  disabled={fsPolicySaving}
                  className="inline-flex h-10 items-center gap-2 rounded-[8px] border border-cyan-200/[0.13] bg-cyan-300/[0.055] px-4 text-sm text-cyan-50 transition hover:bg-cyan-300/[0.085] disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {fsPolicySaving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
                  保存路径策略
                </button>
                {fsPolicySavedHint && <span className="text-xs text-emerald-300">已保存，L3 将重新加载策略文件</span>}
              </div>
            </div>
          </details>
        </motion.section>

        <motion.section
          className="jarvis-panel rounded-[8px] border border-cyan-200/[0.08] bg-cyan-300/[0.018] p-4"
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3, delay: 0.07 }}
        >
          <LarkLongConnectionSettings />
        </motion.section>
      </div>
    </div>
  );
}

function StatusTile({ label, value, icon }: { label: string; value: string; icon: ReactNode }) {
  return (
    <div className="jarvis-tile rounded-[8px] border border-cyan-200/[0.07] bg-slate-950/24 p-3">
      <div className="relative z-10 flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="font-mono text-[10px] uppercase tracking-[0.14em] text-slate-500">{label}</div>
          <div className="mt-1 truncate font-mono text-sm text-slate-100" title={value}>
            {value}
          </div>
        </div>
        <span className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-[7px] border border-cyan-200/[0.08] bg-cyan-300/[0.035] text-cyan-100/80">
          {icon}
        </span>
      </div>
    </div>
  );
}

function ToggleCard({
  title,
  summary,
  checked,
  disabled,
  onChange,
  tone = "cyan",
}: {
  title: string;
  summary: string;
  checked: boolean;
  disabled?: boolean;
  onChange: () => void;
  tone?: "cyan" | "amber";
}) {
  const activeClass =
    tone === "amber"
      ? "border-amber-300/25 bg-amber-300/[0.055]"
      : "border-cyan-200/[0.16] bg-cyan-300/[0.055]";

  return (
    <button
      type="button"
      onClick={onChange}
      disabled={disabled}
      className={cn(
        "jarvis-tile relative min-h-[116px] rounded-[8px] border p-4 text-left transition disabled:cursor-not-allowed disabled:opacity-50",
        checked ? activeClass : "border-cyan-200/[0.07] bg-slate-950/22 hover:border-cyan-200/[0.14]"
      )}
    >
      <div className="relative z-10 flex items-start justify-between gap-4">
        <div>
          <div className="text-sm font-semibold text-slate-100">{title}</div>
          <div className="mt-2 text-xs text-slate-500">{summary}</div>
        </div>
        <span
          className={cn(
            "relative mt-0.5 h-5 w-9 flex-shrink-0 rounded-full border transition-colors",
            checked ? "border-cyan-200/25 bg-cyan-300/20" : "border-cyan-200/[0.09] bg-slate-950/45"
          )}
        >
          <span
            className={cn(
              "absolute top-0.5 h-3.5 w-3.5 rounded-full bg-slate-100 transition-transform",
              checked ? "translate-x-4" : "translate-x-0.5"
            )}
          />
        </span>
      </div>
      <div className="relative z-10 mt-4 font-mono text-[10px] uppercase tracking-[0.14em] text-slate-500">
        {checked ? "armed" : "guarded"}
      </div>
    </button>
  );
}

function SelectControl({
  label,
  value,
  onChange,
  disabled,
  children,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  children: ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-2 block font-mono text-[10px] uppercase tracking-[0.14em] text-slate-500">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        className="h-10 w-full rounded-[8px] border border-cyan-200/[0.1] bg-slate-950/42 px-3 font-mono text-sm text-slate-100 outline-none focus:border-cyan-200/35 disabled:opacity-50"
      >
        {children}
      </select>
    </label>
  );
}

function PolicyBlock({ title, icon, children }: { title: string; icon: ReactNode; children: ReactNode }) {
  return (
    <div className="rounded-[8px] border border-cyan-200/[0.07] bg-slate-950/24 p-4">
      <div className="mb-3 flex items-center gap-2 text-cyan-100/80">
        {icon}
        <span className="text-sm font-medium text-slate-200">{title}</span>
      </div>
      <div className="space-y-3">{children}</div>
    </div>
  );
}

function PathList({ items, empty }: { items: string[]; empty: string }) {
  return (
    <div className="max-h-36 overflow-auto rounded-[8px] border border-cyan-200/[0.06] bg-slate-950/35 p-2 custom-scrollbar">
      {items.length === 0 ? (
        <div className="text-xs text-slate-500">{empty}</div>
      ) : (
        <div className="space-y-1">
          {items.map((item) => (
            <div key={item} className="truncate font-mono text-[11px] text-slate-400" title={item}>
              {item}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function EditablePathList({
  items,
  draft,
  placeholder,
  onDraftChange,
  onAdd,
  onRemove,
  tone = "cyan",
}: {
  items: string[];
  draft: string;
  placeholder: string;
  onDraftChange: (value: string) => void;
  onAdd: () => void;
  onRemove: (value: string) => void;
  tone?: "cyan" | "orange";
}) {
  const buttonClass =
    tone === "orange"
      ? "border-orange-300/25 bg-orange-300/[0.07] text-orange-100 hover:bg-orange-300/[0.11]"
      : "border-cyan-200/[0.13] bg-cyan-300/[0.055] text-cyan-50 hover:bg-cyan-300/[0.085]";

  return (
    <div className="space-y-2">
      {items.length > 0 && (
        <div className="space-y-1">
          {items.map((item) => (
            <div key={item} className="flex items-center gap-2 rounded-[7px] border border-cyan-200/[0.06] bg-cyan-300/[0.018] px-2 py-1">
              <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-slate-300" title={item}>
                {item}
              </span>
              <button type="button" onClick={() => onRemove(item)} className="text-slate-500 hover:text-rose-300" title="移除此项">
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </div>
          ))}
        </div>
      )}
      <div className="flex gap-2">
        <input
          type="text"
          value={draft}
          onChange={(e) => onDraftChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              onAdd();
            }
          }}
          placeholder={placeholder}
          className="h-9 min-w-0 flex-1 rounded-[8px] border border-cyan-200/[0.08] bg-slate-950/42 px-3 font-mono text-xs text-slate-100 outline-none placeholder:text-slate-600 focus:border-cyan-200/28"
        />
        <button type="button" onClick={onAdd} className={cn("inline-flex h-9 items-center gap-1.5 rounded-[8px] border px-3 text-xs transition", buttonClass)}>
          <Plus className="h-3.5 w-3.5" />
          添加
        </button>
      </div>
    </div>
  );
}
