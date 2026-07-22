/** Voice Core UX Profile: same pipeline, different UX shells. */
export type VoiceUxProfile = "wake" | "continuous" | "chat_ptt" | "chat_vad";

export type VoiceProfileConfig = {
  id: VoiceUxProfile;
  /** 唤醒滴声 / 「我在」 */
  wakeAck: boolean;
  /** 绑定 Orb / HUD 陪伴态 */
  companionUi: boolean;
  /** JVS 朗读回复句数上限；0 = 不朗读 */
  maxSpeakSentences: number;
  /** 60s 连续对话窗口（仅 wake 门卫链） */
  conversationWindowSec: number;
};

export const VOICE_PROFILES: Record<VoiceUxProfile, VoiceProfileConfig> = {
  wake: {
    id: "wake",
    wakeAck: true,
    companionUi: true,
    maxSpeakSentences: 3,
    conversationWindowSec: 60,
  },
  continuous: {
    id: "continuous",
    wakeAck: false,
    companionUi: true,
    maxSpeakSentences: 3,
    conversationWindowSec: 0,
  },
  chat_ptt: {
    id: "chat_ptt",
    wakeAck: false,
    companionUi: false,
    maxSpeakSentences: 3,
    conversationWindowSec: 0,
  },
  chat_vad: {
    id: "chat_vad",
    wakeAck: false,
    companionUi: false,
    maxSpeakSentences: 3,
    conversationWindowSec: 0,
  },
};

export function resolveChatSpeakSentences(ttsEnabled: boolean): number {
  return ttsEnabled ? VOICE_PROFILES.chat_ptt.maxSpeakSentences : 0;
}
