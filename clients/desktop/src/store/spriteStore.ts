import { create } from "zustand";
import { persist } from "zustand/middleware";
import { DEFAULT_KOKORO_TTS_VOICE } from "../voice/voiceDefaults";

export type SpriteState = "idle" | "listening" | "thinking" | "speaking" | "happy" | "sad";

/** 精灵形象预设 */
export type AvatarPreset = "rive" | "emoji-default" | "emoji-friendly" | "emoji-tech" | "pixi";

/** Pixi 图集形象 ID（对应 avatars/{id} 目录，支持商店替换） */
export type PixiAvatarId = string;

/** 主题预设 */
export type ThemePreset = "cyber-heart" | "cyber-light" | "cyber-neon";

/** 宠物逻辑动作（用于映射到图集动画） */
export type PetAction =
  | "idle"
  | "touch"
  | "sleep"
  | "blink"
  | "smile"
  | "picked"
  | "hit"
  | "dizzy";

/** 动作 -> 图集动画名 映射（可在设置中修改） */
export type AnimationMapping = Partial<Record<PetAction, string>>;

const DEFAULT_ANIMATION_MAPPING: AnimationMapping = {
  idle: "IDLE",
  touch: "SMILE",
  sleep: "SLEEP",
  blink: "BLINK",
  smile: "SMILE",
  picked: "PICKED",
  hit: "HIT",
  dizzy: "DIZZY",
};

interface SpriteStore {
  state: SpriteState;
  position: { x: number; y: number };
  avatarId: AvatarPreset;
  /** Pixi 模式下使用的形象包 ID（avatars/{pixiAvatarId}） */
  pixiAvatarId: PixiAvatarId;
  themeId: ThemePreset;
  /** Pixi 图集：动作 -> 动画名 映射 */
  animationMapping: AnimationMapping;
  /** 是否启用法线贴图 */
  useNormalMap: boolean;
  /** Pixi 精灵缩放，1=像素级（frameSize 256 即 256px），>1 为故意放大 */
  pixiScale: number;
  /** 空闲多少秒后进入 SLEEP（默认 900=15 分钟） */
  idleToSleepSeconds: number;
  /** 是否启用 TTS 朗读 AI 回复（有扬声器时可用） */
  ttsEnabled: boolean;
  /** TTS voice ID aligned with the Kokoro trace baseline. */
  ttsVoice: string;
  setState: (state: SpriteState) => void;
  setPosition: (position: { x: number; y: number }) => void;
  setAvatarId: (id: AvatarPreset) => void;
  setPixiAvatarId: (id: PixiAvatarId) => void;
  setThemeId: (id: ThemePreset) => void;
  setAnimationMapping: (mapping: Partial<AnimationMapping>) => void;
  setUseNormalMap: (use: boolean) => void;
  setPixiScale: (scale: number) => void;
  setIdleToSleepSeconds: (seconds: number) => void;
  setTtsEnabled: (enabled: boolean) => void;
  setTtsVoice: (voice: string) => void;
  /** 根据动作获取图集动画名 */
  getAtlasAnimation: (action: PetAction) => string;
}

export const useSpriteStore = create<SpriteStore>()(
  persist(
    (set, get) => ({
      state: "idle",
      position: { x: 100, y: 100 },
      avatarId: "pixi", // 默认 Pixi 图集（core-atlas 已存在），Rive 需 jachin_sprite.riv
      pixiAvatarId: "core",
      themeId: "cyber-heart",
      animationMapping: DEFAULT_ANIMATION_MAPPING,
      useNormalMap: true,
      pixiScale: 1,
      idleToSleepSeconds: 900,
      ttsEnabled: true,
      ttsVoice: DEFAULT_KOKORO_TTS_VOICE,
      setState: (state) => set({ state }),
      setPosition: (position) => set({ position }),
      setAvatarId: (avatarId) => set({ avatarId }),
      setPixiAvatarId: (pixiAvatarId) => set({ pixiAvatarId }),
      setThemeId: (themeId) => set({ themeId }),
      setAnimationMapping: (mapping) =>
        set({ animationMapping: { ...get().animationMapping, ...mapping } }),
      setUseNormalMap: (useNormalMap) => set({ useNormalMap }),
      setPixiScale: (pixiScale) => set({ pixiScale }),
      setIdleToSleepSeconds: (idleToSleepSeconds) => set({ idleToSleepSeconds }),
      setTtsEnabled: (ttsEnabled) => set({ ttsEnabled }),
      setTtsVoice: (ttsVoice) => set({ ttsVoice }),
      getAtlasAnimation: (action) => {
        const mapped = get().animationMapping[action];
        return mapped ?? DEFAULT_ANIMATION_MAPPING[action] ?? "IDLE";
      },
    }),
    { name: "jachin-sprite-persona" }
  )
);
