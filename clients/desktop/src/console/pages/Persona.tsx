/**
 * Persona - 形象与声音个性化设置
 */

import { useEffect, useMemo, useState } from "react";
import { motion } from "framer-motion";
import {
  CheckCircle2,
  Loader2,
  Mic,
  Palette,
  Radio,
  SlidersHorizontal,
  User,
  Volume2,
  Wand2,
} from "lucide-react";
import { listVoices } from "../../lib/api";
import { getAvailableAvatars } from "../../config/avatars";
import { useSpriteStore, type AvatarPreset, type PetAction, type ThemePreset } from "../../store/spriteStore";
import { cn } from "../../utils/cn";

const AVATAR_OPTIONS: { id: AvatarPreset; label: string; short: string; desc: string }[] = [
  { id: "rive", label: "Rive 动画", short: "RV", desc: "流体动画形象" },
  { id: "pixi", label: "Pixi 图集", short: "PX", desc: "像素级可映射动作" },
  { id: "emoji-default", label: "默认", short: "J", desc: "轻量默认形象" },
  { id: "emoji-friendly", label: "友好", short: "FR", desc: "温和陪伴语气" },
  { id: "emoji-tech", label: "科技", short: "TC", desc: "更强控制台风格" },
];

const ATLAS_ANIMATIONS = ["IDLE", "SLEEP", "SMILE", "BLINK", "PICKED", "HIT", "DIZZY"] as const;

const PET_ACTIONS: { action: PetAction; label: string }[] = [
  { action: "idle", label: "空闲" },
  { action: "sleep", label: "睡眠" },
  { action: "touch", label: "点击" },
  { action: "smile", label: "微笑" },
  { action: "blink", label: "眨眼" },
  { action: "picked", label: "拾起" },
  { action: "hit", label: "受击" },
  { action: "dizzy", label: "眩晕" },
];

const THEME_OPTIONS: { id: ThemePreset; label: string; desc: string; swatch: string }[] = [
  { id: "cyber-heart", label: "Cyber Heart", desc: "深色心核", swatch: "from-rose-400/65 via-fuchsia-300/30 to-cyan-300/45" },
  { id: "cyber-light", label: "Cyber Light", desc: "浅色晶体", swatch: "from-cyan-200/70 via-sky-100/45 to-emerald-200/55" },
  { id: "cyber-neon", label: "Cyber Neon", desc: "霓虹高能", swatch: "from-cyan-300/70 via-violet-300/50 to-fuchsia-400/55" },
];

export function Persona() {
  const {
    avatarId,
    pixiAvatarId,
    setAvatarId,
    setPixiAvatarId,
    themeId,
    setThemeId,
    setAnimationMapping,
    useNormalMap,
    setUseNormalMap,
    pixiScale,
    setPixiScale,
    idleToSleepSeconds,
    setIdleToSleepSeconds,
    ttsEnabled,
    setTtsEnabled,
    ttsVoice,
    setTtsVoice,
    getAtlasAnimation,
  } = useSpriteStore();
  const pixiAvatars = getAvailableAvatars([]);
  const [voices, setVoices] = useState<Record<string, unknown>[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const load = async () => {
      try {
        const res = await listVoices("zh-CN");
        setVoices(res.voices ?? []);
      } catch {
        setVoices([]);
      } finally {
        setLoading(false);
      }
    };
    void load();
  }, []);

  const currentAvatar = useMemo(
    () => AVATAR_OPTIONS.find((item) => item.id === avatarId) ?? AVATAR_OPTIONS[0],
    [avatarId]
  );
  const currentTheme = useMemo(
    () => THEME_OPTIONS.find((item) => item.id === themeId) ?? THEME_OPTIONS[0],
    [themeId]
  );

  return (
    <div className="persona-page h-full overflow-auto p-5 sm:p-6">
      <div className="mx-auto flex max-w-[1180px] flex-col gap-5">
        <motion.header
          className="jarvis-panel relative overflow-hidden rounded-[8px] border border-cyan-200/[0.08] bg-cyan-300/[0.018] p-5"
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.32, ease: [0.22, 1, 0.36, 1] }}
        >
          <div className="jarvis-hero-grid opacity-[0.22]" aria-hidden />
          <div className="relative z-10 grid gap-5 lg:grid-cols-[minmax(0,1fr)_340px]">
            <div className="flex items-center gap-5">
              <div className="jarvis-core-stage relative flex h-28 w-28 flex-shrink-0 items-center justify-center">
                <svg className="jarvis-core-svg" viewBox="0 0 260 260" aria-hidden>
                  <circle className="jarvis-core-ring jarvis-core-ring-outer" cx="130" cy="130" r="108" />
                  <circle className="jarvis-core-ring jarvis-core-ring-mid" cx="130" cy="130" r="82" />
                  <circle className="jarvis-core-ring jarvis-core-ring-inner" cx="130" cy="130" r="58" />
                  <path className="jarvis-core-arc jarvis-core-arc-a" d="M130 22a108 108 0 0 1 99 65" />
                  <path className="jarvis-core-arc jarvis-core-arc-b" d="M51 204a108 108 0 0 1 0-148" />
                </svg>
                <motion.div
                  className="flex h-16 w-16 items-center justify-center rounded-full border border-cyan-100/20 bg-cyan-300/[0.055] font-mono text-lg font-semibold text-cyan-50 shadow-[0_0_32px_rgba(56,189,248,0.12)]"
                  animate={{ scale: [1, 1.035, 1] }}
                  transition={{ duration: 3.2, repeat: Infinity, ease: "easeInOut" }}
                >
                  {currentAvatar.short}
                </motion.div>
                <div className="jarvis-core-scan" aria-hidden />
              </div>
              <div className="min-w-0">
                <p className="mb-2 inline-flex rounded-full border border-cyan-200/[0.09] bg-cyan-300/[0.035] px-3 py-1 font-mono text-[10px] uppercase tracking-[0.18em] text-cyan-100/75">
                  Persona Core
                </p>
                <h1 className="text-2xl font-semibold text-slate-100 sm:text-3xl">Persona</h1>
                <p className="mt-2 max-w-xl text-sm leading-6 text-slate-400">
                  调整 Jachin 的声音、形象和控制台主题。默认只展示关键操作，高级映射按需展开。
                </p>
                <div className="mt-4 flex flex-wrap gap-2">
                  <StatusPill label="Avatar" value={currentAvatar.label} />
                  <StatusPill label="Theme" value={currentTheme.label} />
                  <StatusPill label="TTS" value={ttsEnabled ? "On" : "Off"} tone={ttsEnabled ? "cyan" : "muted"} />
                </div>
              </div>
            </div>

            <div className="grid gap-3">
              <div className="jarvis-tile rounded-[8px] border border-cyan-200/[0.07] bg-slate-950/24 p-4">
                <div className="relative z-10 flex items-center justify-between">
                  <span className="text-xs text-slate-500">当前主题</span>
                  <Palette className="h-4 w-4 text-cyan-100/75" />
                </div>
                <div className={cn("relative z-10 mt-4 h-12 rounded-[8px] bg-gradient-to-r", currentTheme.swatch)} />
                <div className="relative z-10 mt-3 text-sm font-medium text-slate-100">{currentTheme.label}</div>
              </div>
              <button
                type="button"
                onClick={() => setTtsEnabled(!ttsEnabled)}
                className={cn(
                  "jarvis-tile relative flex items-center justify-between rounded-[8px] border p-4 text-left transition",
                  ttsEnabled
                    ? "border-cyan-200/[0.14] bg-cyan-300/[0.05]"
                    : "border-cyan-200/[0.07] bg-slate-950/24 hover:border-cyan-200/[0.16]"
                )}
              >
                <span className="relative z-10">
                  <span className="block text-sm font-medium text-slate-100">AI 回复朗读</span>
                  <span className="mt-1 block text-xs text-slate-500">{ttsEnabled ? "语音输出已启用" : "保持静默模式"}</span>
                </span>
                <ToggleVisual checked={ttsEnabled} />
              </button>
            </div>
          </div>
        </motion.header>

        <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_340px]">
          <main className="flex min-w-0 flex-col gap-5">
            <motion.section
              className="jarvis-panel rounded-[8px] border border-cyan-200/[0.08] bg-cyan-300/[0.018] p-4"
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.32, delay: 0.04, ease: [0.22, 1, 0.36, 1] }}
            >
              <SectionTitle icon={<User className="h-4 w-4" />} label="Avatar Matrix" hint="选择后立即生效" />
              <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                {AVATAR_OPTIONS.map((option) => (
                  <motion.button
                    layout
                    key={option.id}
                    type="button"
                    onClick={() => setAvatarId(option.id)}
                    whileHover={{ y: -2 }}
                    whileTap={{ scale: 0.985 }}
                    className={cn(
                      "jarvis-tile relative min-h-[120px] rounded-[8px] border p-4 text-left transition",
                      avatarId === option.id
                        ? "border-cyan-200/[0.18] bg-cyan-300/[0.06]"
                        : "border-cyan-200/[0.07] bg-slate-950/24 hover:border-cyan-200/[0.15]"
                    )}
                  >
                    <div className="relative z-10 flex items-start justify-between">
                      <div>
                        <div className="text-sm font-semibold text-slate-100">{option.label}</div>
                        <div className="mt-2 text-xs leading-5 text-slate-500">{option.desc}</div>
                      </div>
                      <span className="flex h-9 w-9 items-center justify-center rounded-[8px] border border-cyan-200/[0.09] bg-cyan-300/[0.035] font-mono text-xs text-cyan-100">
                        {option.short}
                      </span>
                    </div>
                    {avatarId === option.id && (
                      <motion.div layoutId="avatar-active" className="absolute bottom-3 right-3 z-10 text-cyan-100">
                        <CheckCircle2 className="h-4 w-4" />
                      </motion.div>
                    )}
                  </motion.button>
                ))}
              </div>

              {avatarId === "pixi" && (
                <motion.div
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: "auto" }}
                  transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
                  className="mt-4 overflow-hidden border-t border-cyan-200/[0.055] pt-4"
                >
                  <div className="grid gap-4 lg:grid-cols-[minmax(0,0.95fr)_minmax(280px,0.8fr)]">
                    <div className="rounded-[8px] border border-cyan-200/[0.07] bg-slate-950/24 p-4">
                      <SectionTitle icon={<Wand2 className="h-4 w-4" />} label="Pixi Package" compact />
                      <div className="mt-3 flex flex-wrap gap-2">
                        {pixiAvatars.map((avatar) => (
                          <button
                            key={avatar.id}
                            type="button"
                            onClick={() => avatar.owned && setPixiAvatarId(avatar.id)}
                            disabled={!avatar.owned}
                            className={cn(
                              "rounded-[8px] border px-3 py-2 text-sm transition",
                              pixiAvatarId === avatar.id
                                ? "border-cyan-200/[0.18] bg-cyan-300/[0.07] text-cyan-50"
                                : avatar.owned
                                  ? "border-cyan-200/[0.08] bg-cyan-300/[0.02] text-slate-400 hover:border-cyan-200/[0.16] hover:text-slate-100"
                                  : "cursor-not-allowed border-cyan-200/[0.04] bg-slate-950/18 text-slate-600"
                            )}
                          >
                            {avatar.name}
                            {avatar.premium && !avatar.owned && <span className="ml-1 text-amber-200/70">premium</span>}
                          </button>
                        ))}
                      </div>
                    </div>

                    <div className="rounded-[8px] border border-cyan-200/[0.07] bg-slate-950/24 p-4">
                      <SectionTitle icon={<SlidersHorizontal className="h-4 w-4" />} label="Sprite Tuning" compact />
                      <div className="mt-4 space-y-4">
                        <label className="flex items-center justify-between gap-3">
                          <span className="text-sm text-slate-300">法线贴图</span>
                          <button type="button" onClick={() => setUseNormalMap(!useNormalMap)} className="flex-shrink-0">
                            <ToggleVisual checked={useNormalMap} />
                          </button>
                        </label>
                        <div>
                          <div className="mb-2 flex items-center justify-between text-sm">
                            <span className="text-slate-300">缩放</span>
                            <span className="font-mono text-xs text-slate-500">{pixiScale === 1 ? "1:1" : `${pixiScale.toFixed(1)}x`}</span>
                          </div>
                          <input
                            type="range"
                            min="0.5"
                            max="3"
                            step="0.1"
                            value={pixiScale}
                            onChange={(e) => setPixiScale(parseFloat(e.target.value))}
                            className="w-full accent-cyan-300"
                          />
                        </div>
                        <label className="block">
                          <span className="mb-2 block text-sm text-slate-300">空闲休眠</span>
                          <select
                            value={idleToSleepSeconds}
                            onChange={(e) => setIdleToSleepSeconds(Number(e.target.value))}
                            className="h-10 w-full rounded-[8px] border border-cyan-200/[0.1] bg-slate-950/42 px-3 font-mono text-sm text-slate-100 outline-none focus:border-cyan-200/35"
                          >
                            <option value={60}>1 分钟</option>
                            <option value={300}>5 分钟</option>
                            <option value={600}>10 分钟</option>
                            <option value={900}>15 分钟</option>
                            <option value={1800}>30 分钟</option>
                            <option value={3600}>60 分钟</option>
                          </select>
                        </label>
                      </div>
                    </div>
                  </div>

                  <details className="mt-4 rounded-[8px] border border-cyan-200/[0.07] bg-slate-950/24">
                    <summary className="cursor-pointer list-none px-4 py-3 font-mono text-[11px] uppercase tracking-[0.14em] text-cyan-100/75">
                      Action Mapping
                    </summary>
                    <div className="grid gap-2 border-t border-cyan-200/[0.055] p-4 sm:grid-cols-2 lg:grid-cols-4">
                      {PET_ACTIONS.map(({ action, label }) => (
                        <label key={action} className="block">
                          <span className="mb-1 block text-xs text-slate-500">{label}</span>
                          <select
                            value={getAtlasAnimation(action)}
                            onChange={(e) => setAnimationMapping({ [action]: e.target.value as (typeof ATLAS_ANIMATIONS)[number] })}
                            className="h-9 w-full rounded-[8px] border border-cyan-200/[0.08] bg-slate-950/42 px-2 font-mono text-xs text-slate-100 outline-none focus:border-cyan-200/28"
                          >
                            {ATLAS_ANIMATIONS.map((animation) => (
                              <option key={animation} value={animation}>
                                {animation}
                              </option>
                            ))}
                          </select>
                        </label>
                      ))}
                    </div>
                  </details>
                </motion.div>
              )}
            </motion.section>

            <motion.section
              className="jarvis-panel rounded-[8px] border border-cyan-200/[0.08] bg-cyan-300/[0.018] p-4"
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.32, delay: 0.08, ease: [0.22, 1, 0.36, 1] }}
            >
              <SectionTitle icon={<Volume2 className="h-4 w-4" />} label="Voice Link" hint="TTS 声音选择" />
              <div className="mt-4 rounded-[8px] border border-cyan-200/[0.07] bg-slate-950/24 p-4">
                {loading ? (
                  <div className="flex items-center gap-2 text-sm text-slate-400">
                    <Loader2 className="h-4 w-4 animate-spin text-cyan-100" />
                    正在加载语音列表
                  </div>
                ) : voices.length === 0 ? (
                  <div className="text-sm text-slate-500">无法获取语音列表，请确认后端语音服务已配置。</div>
                ) : (
                  <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_180px]">
                    <select
                      value={ttsVoice}
                      onChange={(e) => setTtsVoice(e.target.value)}
                      className="h-10 w-full rounded-[8px] border border-cyan-200/[0.1] bg-slate-950/42 px-3 font-mono text-sm text-slate-100 outline-none focus:border-cyan-200/35"
                    >
                      {voices.slice(0, 30).map((voice, index) => {
                        const voiceId = String(voice.ShortName ?? voice.Name ?? voice.id ?? index);
                        const displayName = String(voice.FriendlyName ?? voice.name ?? voice.id ?? voiceId);
                        return (
                          <option key={voiceId} value={voiceId}>
                            {displayName}
                          </option>
                        );
                      })}
                    </select>
                    <div className="flex items-center gap-2 rounded-[8px] border border-cyan-200/[0.07] bg-cyan-300/[0.02] px-3 text-xs text-slate-500">
                      <Radio className="h-3.5 w-3.5 text-cyan-100/60" />
                      {voices.length > 30 ? `显示 30 / ${voices.length}` : `${voices.length} voices`}
                    </div>
                  </div>
                )}
              </div>
            </motion.section>
          </main>

          <motion.aside
            className="jarvis-panel rounded-[8px] border border-cyan-200/[0.08] bg-cyan-300/[0.018] p-4"
            initial={{ opacity: 0, x: 16 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ duration: 0.34, delay: 0.12, ease: [0.22, 1, 0.36, 1] }}
          >
            <SectionTitle icon={<Palette className="h-4 w-4" />} label="Theme Reactor" hint="切换后立即生效" />
            <div className="mt-4 flex flex-col gap-3">
              {THEME_OPTIONS.map((theme) => (
                <motion.button
                  layout
                  key={theme.id}
                  type="button"
                  onClick={() => setThemeId(theme.id)}
                  whileHover={{ y: -2 }}
                  whileTap={{ scale: 0.985 }}
                  className={cn(
                    "jarvis-tile relative overflow-hidden rounded-[8px] border p-4 text-left transition",
                    themeId === theme.id
                      ? "border-cyan-200/[0.18] bg-cyan-300/[0.06]"
                      : "border-cyan-200/[0.07] bg-slate-950/24 hover:border-cyan-200/[0.15]"
                  )}
                >
                  <div className={cn("relative z-10 h-16 rounded-[8px] bg-gradient-to-r", theme.swatch)} />
                  <div className="relative z-10 mt-3 flex items-center justify-between">
                    <div>
                      <div className="text-sm font-semibold text-slate-100">{theme.label}</div>
                      <div className="mt-1 text-xs text-slate-500">{theme.desc}</div>
                    </div>
                    {themeId === theme.id && <CheckCircle2 className="h-4 w-4 text-cyan-100" />}
                  </div>
                </motion.button>
              ))}
            </div>
          </motion.aside>
        </div>
      </div>
    </div>
  );
}

function SectionTitle({ icon, label, hint, compact = false }: { icon: React.ReactNode; label: string; hint?: string; compact?: boolean }) {
  return (
    <div className="flex items-center gap-2">
      <span className="flex h-8 w-8 items-center justify-center rounded-[7px] border border-cyan-200/[0.08] bg-cyan-300/[0.035] text-cyan-100/80">
        {icon}
      </span>
      <div>
        <div className="font-mono text-[11px] uppercase tracking-[0.16em] text-cyan-100/80">{label}</div>
        {!compact && hint && <div className="mt-0.5 text-xs text-slate-500">{hint}</div>}
      </div>
    </div>
  );
}

function StatusPill({ label, value, tone = "cyan" }: { label: string; value: string; tone?: "cyan" | "muted" }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-2 rounded-full border px-2.5 py-1 font-mono text-[10px] uppercase tracking-[0.1em]",
        tone === "cyan" ? "border-cyan-200/[0.1] bg-cyan-300/[0.035] text-cyan-50/80" : "border-cyan-200/[0.06] bg-slate-950/24 text-slate-500"
      )}
    >
      <span className="text-slate-500">{label}</span>
      {value}
    </span>
  );
}

function ToggleVisual({ checked }: { checked: boolean }) {
  return (
    <span
      className={cn(
        "relative z-10 h-5 w-9 flex-shrink-0 rounded-full border transition-colors",
        checked ? "border-cyan-200/25 bg-cyan-300/20" : "border-cyan-200/[0.09] bg-slate-950/45"
      )}
    >
      <motion.span
        className="absolute top-0.5 h-3.5 w-3.5 rounded-full bg-slate-100"
        animate={{ x: checked ? 18 : 2 }}
        transition={{ type: "spring", stiffness: 420, damping: 32 }}
      />
    </span>
  );
}
