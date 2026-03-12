/**
 * Persona - 形象与声音个性化设置（HUD 风格）
 */

import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import { Palette, Mic, User, Loader2 } from "lucide-react";
import { listVoices } from "../../lib/api";
import { useSpriteStore, type AvatarPreset, type ThemePreset, type PetAction } from "../../store/spriteStore";
import { getAvailableAvatars } from "../../config/avatars";
import { cn } from "../../utils/cn";

const AVATAR_OPTIONS: { id: AvatarPreset; label: string; preview: string }[] = [
  { id: "rive", label: "Rive 动画", preview: "🎬" },
  { id: "pixi", label: "Pixi 图集", preview: "🖼" },
  { id: "emoji-default", label: "默认", preview: "🤖" },
  { id: "emoji-friendly", label: "友好", preview: "😊" },
  { id: "emoji-tech", label: "科技", preview: "🔮" },
];

const ATLAS_ANIMATIONS = ["IDLE", "SLEEP", "SMILE", "BLINK", "PICKED", "HIT", "DIZZY"] as const;
const PET_ACTIONS: { action: PetAction; label: string }[] = [
  { action: "idle", label: "空闲" },
  { action: "sleep", label: "睡眠" },
  { action: "touch", label: "点击" },
  { action: "smile", label: "微笑" },
  { action: "blink", label: "眨眼" },
  { action: "picked", label: "被拾起" },
  { action: "hit", label: "受击" },
  { action: "dizzy", label: "眩晕" },
];

const THEME_OPTIONS: { id: ThemePreset; label: string; desc: string }[] = [
  { id: "cyber-heart", label: "Cyber Heart", desc: "深色主调" },
  { id: "cyber-light", label: "Cyber Light", desc: "浅色主调" },
  { id: "cyber-neon", label: "Cyber Neon", desc: "霓虹强调" },
];

export function Persona() {
  const { avatarId, pixiAvatarId, setAvatarId, setPixiAvatarId, themeId, setThemeId, animationMapping, setAnimationMapping, useNormalMap, setUseNormalMap, pixiScale, setPixiScale, idleToSleepSeconds, setIdleToSleepSeconds, ttsEnabled, setTtsEnabled, ttsVoice, setTtsVoice, getAtlasAnimation } = useSpriteStore();
  const pixiAvatars = getAvailableAvatars([]); // TODO: 从商店 API 传入已购买 ID 列表
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
    load();
  }, []);

  return (
    <div className="h-full flex flex-col p-6 overflow-auto">
      <header className="flex-shrink-0 mb-6">
        <h1
          className="font-sci-fi text-2xl font-bold text-transparent bg-clip-text bg-gradient-to-r from-rose-400 to-rose-600"
          style={{ fontFamily: "Orbitron, sans-serif" }}
        >
          Persona
        </h1>
        <p className="text-slate-500 text-sm mt-0.5">形象与声音个性化设置</p>
      </header>

      {/* 左右分栏：左侧 语音+形象，右侧 主题，避免重叠与悬停冲突；窄屏时上下堆叠 */}
      <div className="flex-1 min-h-0 flex flex-col lg:flex-row gap-6">
        {/* 左侧：语音 + 精灵形象 */}
        <div className="flex-1 min-w-0 flex flex-col gap-6 overflow-auto">
          <motion.section
            className="glass-panel rounded-xl overflow-hidden flex flex-col min-h-0 flex-shrink-0"
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.3 }}
          >
            <div className="flex-shrink-0 px-4 py-3 border-b border-white/10 flex items-center gap-2">
              <Mic className="w-4 h-4 text-rose-400/80" />
              <span className="font-mono text-xs uppercase tracking-wider text-slate-400">
                语音 (TTS)
              </span>
            </div>
            <div className="flex-1 overflow-auto p-4 min-h-0 space-y-4">
              <div className="flex items-center gap-2">
                <input
                  type="checkbox"
                  id="ttsEnabled"
                  checked={ttsEnabled}
                  onChange={(e) => setTtsEnabled(e.target.checked)}
                  className="rounded border-white/20"
                />
                <label htmlFor="ttsEnabled" className="text-sm font-mono text-slate-400">
                  朗读 AI 回复（有扬声器时使用 TTS）
                </label>
              </div>
              {loading ? (
                <div className="flex items-center gap-2 text-slate-400 font-mono text-sm">
                  <Loader2 className="w-4 h-4 animate-spin" />
                  加载中...
                </div>
              ) : voices.length === 0 ? (
                <p className="text-slate-500 text-sm font-mono">
                  无法获取语音列表，请确保后端语音服务已配置。
                </p>
              ) : (
                <div>
                  <label htmlFor="ttsVoice" className="text-slate-400 text-xs font-mono mb-2 block">
                    选择语音
                  </label>
                  <select
                    id="ttsVoice"
                    value={ttsVoice}
                    onChange={(e) => setTtsVoice(e.target.value)}
                    className="w-full px-3 py-2 rounded border border-white/10 bg-white/5 text-slate-300 text-sm font-mono"
                  >
                    {voices.slice(0, 30).map((v, i) => {
                      const voiceId = String(v.ShortName ?? v.Name ?? v.id ?? i);
                      const displayName = String(v.FriendlyName ?? v.name ?? v.id ?? voiceId);
                      return (
                        <option key={voiceId} value={voiceId}>
                          {displayName}
                        </option>
                      );
                    })}
                  </select>
                  {voices.length > 30 && (
                    <p className="text-slate-500 text-xs font-mono mt-1">共 {voices.length} 个语音，显示前 30 个</p>
                  )}
                </div>
              )}
            </div>
          </motion.section>

          <motion.section
            className="glass-panel rounded-xl p-5 flex flex-col flex-shrink-0"
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.3, delay: 0.05 }}
          >
            <div className="flex-shrink-0 flex items-center gap-2 mb-3">
              <User className="w-4 h-4 text-rose-400/80" />
              <span className="font-mono text-xs uppercase tracking-wider text-slate-400">
                精灵形象
              </span>
            </div>
            <p className="text-slate-400 text-sm font-mono leading-relaxed mb-4">
              桌面精灵的动画与外观在精灵窗口中展示，选择形象后立即生效。
            </p>
            <div className="flex flex-wrap gap-2">
              {AVATAR_OPTIONS.map((opt) => (
                <button
                  key={opt.id}
                  type="button"
                  onClick={() => setAvatarId(opt.id)}
                  className={cn(
                    "flex items-center gap-2 px-3 py-2 rounded-lg border font-mono text-sm transition-colors",
                    avatarId === opt.id
                      ? "border-rose-500/60 bg-rose-500/20 text-rose-300"
                      : "border-white/10 bg-white/5 text-slate-400 hover:border-white/20 hover:bg-white/10"
                  )}
                >
                  <span className="text-lg">{opt.preview}</span>
                  {opt.label}
                </button>
              ))}
            </div>

            {/* Pixi 图集：形象包选择 + 动作映射 + 法线贴图 */}
            {avatarId === "pixi" && (
              <div className="mt-4 pt-4 border-t border-white/10 space-y-4">
                <div>
                  <p className="text-slate-400 text-xs font-mono mb-2">形象包（可替换，支持商店购买）</p>
                  <div className="flex flex-wrap gap-2">
                    {pixiAvatars.map((a) => (
                      <button
                        key={a.id}
                        type="button"
                        onClick={() => a.owned && setPixiAvatarId(a.id)}
                        disabled={!a.owned}
                        className={cn(
                          "flex items-center gap-2 px-3 py-2 rounded-lg border font-mono text-sm transition-colors",
                          pixiAvatarId === a.id
                            ? "border-rose-500/60 bg-rose-500/20 text-rose-300"
                            : a.owned
                              ? "border-white/10 bg-white/5 text-slate-400 hover:border-white/20"
                              : "border-white/5 text-slate-600 cursor-not-allowed opacity-60"
                        )}
                      >
                        {a.name}
                        {a.premium && !a.owned && (
                          <span className="text-amber-400 text-xs">(需购买)</span>
                        )}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    id="useNormalMap"
                    checked={useNormalMap}
                    onChange={(e) => setUseNormalMap(e.target.checked)}
                    className="rounded border-white/20"
                  />
                  <label htmlFor="useNormalMap" className="text-sm font-mono text-slate-400">
                    启用法线贴图
                  </label>
                </div>
                <div className="flex items-center gap-2">
                  <label htmlFor="pixiScale" className="text-sm font-mono text-slate-400 shrink-0">
                    精灵缩放
                  </label>
                  <input
                    type="range"
                    id="pixiScale"
                    min="0.5"
                    max="3"
                    step="0.1"
                    value={pixiScale}
                    onChange={(e) => setPixiScale(parseFloat(e.target.value))}
                    className="flex-1"
                  />
                  <span className="text-slate-500 text-xs font-mono w-8">
                    {pixiScale === 1 ? "1:1" : pixiScale.toFixed(1) + "×"}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <label htmlFor="idleToSleep" className="text-sm font-mono text-slate-400 shrink-0">
                    空闲休眠
                  </label>
                  <select
                    id="idleToSleep"
                    value={idleToSleepSeconds}
                    onChange={(e) => setIdleToSleepSeconds(Number(e.target.value))}
                    className="flex-1 px-2 py-1 rounded border border-white/10 bg-white/5 text-slate-300 text-xs font-mono"
                  >
                    <option value={60}>1 分钟</option>
                    <option value={300}>5 分钟</option>
                    <option value={600}>10 分钟</option>
                    <option value={900}>15 分钟</option>
                    <option value={1800}>30 分钟</option>
                    <option value={3600}>60 分钟</option>
                  </select>
                </div>
                <div>
                  <p className="text-slate-400 text-xs font-mono mb-2">动作 → 图集动画映射</p>
                  <div className="grid grid-cols-2 gap-2">
                    {PET_ACTIONS.map(({ action, label }) => (
                      <div key={action} className="flex items-center gap-2">
                        <span className="text-slate-500 text-xs w-14 truncate">{label}</span>
                        <select
                          value={getAtlasAnimation(action)}
                          onChange={(e) => setAnimationMapping({ [action]: e.target.value as typeof ATLAS_ANIMATIONS[number] })}
                          className="flex-1 px-2 py-1 rounded border border-white/10 bg-white/5 text-slate-300 text-xs font-mono"
                        >
                          {ATLAS_ANIMATIONS.map((anim) => (
                            <option key={anim} value={anim}>
                              {anim}
                            </option>
                          ))}
                        </select>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            )}
          </motion.section>
        </div>

        {/* 右侧：主题（独立区域，避免与左侧重叠） */}
        <motion.section
          className="lg:w-72 flex-shrink-0 glass-panel rounded-xl p-5 flex flex-col relative z-10"
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3, delay: 0.1 }}
        >
          <div className="flex items-center gap-2 mb-2">
            <Palette className="w-4 h-4 text-rose-400/80" />
            <span className="font-mono text-xs uppercase tracking-wider text-slate-400">
              主题
            </span>
          </div>
          <p className="text-slate-400 text-sm font-mono mb-4">
            控制台主题选择，切换后立即生效。
          </p>
          <div className="flex flex-col gap-2">
            {THEME_OPTIONS.map((opt) => {
              const isAvailable = true;
              return (
                <button
                  key={opt.id}
                  type="button"
                  onClick={() => isAvailable && setThemeId(opt.id)}
                  disabled={!isAvailable}
                  className={cn(
                    "flex flex-col items-start px-4 py-3 rounded-lg border font-mono text-sm transition-colors text-left w-full",
                    themeId === opt.id
                      ? "border-rose-500/60 bg-rose-500/20 text-rose-300"
                      : isAvailable
                        ? "border-white/10 bg-white/5 text-slate-400 hover:border-white/20 hover:bg-white/10"
                        : "border-white/5 bg-white/5 text-slate-600 cursor-not-allowed opacity-60"
                  )}
                >
                  <span>{opt.label}</span>
                  <span className="text-xs mt-0.5 opacity-80">{opt.desc}</span>
                </button>
              );
            })}
          </div>
        </motion.section>
      </div>
    </div>
  );
}
