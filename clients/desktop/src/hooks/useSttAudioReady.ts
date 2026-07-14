import { useEffect, useRef } from "react";
import { listen } from "@tauri-apps/api/event";

const STT_AUDIO_READY = "STT_AUDIO_READY";
const STT_PTT_FAILED = "STT_PTT_FAILED";

export interface SttAudioPayload {
  wav_base64: string;
  recognized_text?: string;
  recognized_finalized?: boolean;
  recognized_source?: string;
}

export interface SttPttFailedPayload {
  reason: string;
  chunks: number;
  detail: string;
}

export type SttAudioReadyOptions = {
  /** 收到事件后是否自动播放（用于验证截断效果） */
  playOnReady?: boolean;
  /** 收到 WAV 时的回调 */
  onReady?: (payload: SttAudioPayload) => void;
  /** PTT 未能产出音频 */
  onPttFailed?: (payload: SttPttFailedPayload) => void;
};

/**
 * 监听 VAD/PTT 截断完成事件 STT_AUDIO_READY，以及 PTT 失败 STT_PTT_FAILED。
 * 回调经 ref 持有，避免父组件重渲染时反复 unlisten 导致事件丢失。
 */
export function useSttAudioReady(options?: SttAudioReadyOptions) {
  const optionsRef = useRef(options);
  optionsRef.current = options;

  useEffect(() => {
    const unlistenReady = listen<SttAudioPayload>(STT_AUDIO_READY, (event) => {
      const payload = event.payload;
      if (!payload?.wav_base64) return;

      const { playOnReady = true, onReady } = optionsRef.current ?? {};
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

    const unlistenFailed = listen<SttPttFailedPayload>(STT_PTT_FAILED, (event) => {
      const payload = event.payload;
      if (!payload) return;
      optionsRef.current?.onPttFailed?.(payload);
    });

    return () => {
      void unlistenReady.then((unlisten) => unlisten());
      void unlistenFailed.then((unlisten) => unlisten());
    };
  }, []);
}
