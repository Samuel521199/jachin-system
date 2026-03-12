import { useEffect } from "react";
import { listen } from "@tauri-apps/api/event";

const STT_AUDIO_READY = "STT_AUDIO_READY";

export interface SttAudioPayload {
  wav_base64: string;
}

/**
 * 监听 VAD 截断完成事件 STT_AUDIO_READY。
 * 收到后可将 wav_base64 转为 Blob/Audio 播放或送 STT API。
 * 调试时可开启 playOnReady，直接播放刚截断的语音以验证截断是否干净。
 */
export function useSttAudioReady(options?: {
  /** 收到事件后是否自动播放（用于验证截断效果） */
  playOnReady?: boolean;
  /** 收到事件时的回调，可用于送 Layer2 STT 等 */
  onReady?: (payload: SttAudioPayload) => void;
}) {
  const { playOnReady = true, onReady } = options ?? {};

  useEffect(() => {
    const unlistenPromise = listen<SttAudioPayload>(STT_AUDIO_READY, (event) => {
      const payload = event.payload;
      if (!payload?.wav_base64) return;

      onReady?.(payload);

      if (playOnReady) {
        try {
          const dataUrl = `data:audio/wav;base64,${payload.wav_base64}`;
          const audio = new Audio(dataUrl);
          audio.play().catch((e) => console.warn("[STT_AUDIO_READY] play failed", e));
        } catch (e) {
          console.warn("[STT_AUDIO_READY] create Audio failed", e);
        }
      }
    });

    return () => {
      unlistenPromise.then((unlisten) => unlisten());
    };
  }, [playOnReady, onReady]);
}
