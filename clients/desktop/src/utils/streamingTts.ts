/**
 * 流式 TTS 工具
 * 按句拆分、排队播放
 */

/** 按句拆分：返回完整句子数组 + 未完成余量 */
export function extractCompleteSentences(
  text: string
): { complete: string[]; remainder: string } {
  if (!text.trim()) return { complete: [], remainder: "" };
  const parts = text.split(/(?<=[。！？；\n.!?])/g);
  const remainder = parts.pop() ?? "";
  const complete = parts.filter((s) => s.trim().length > 0);
  return { complete, remainder };
}

/** 音频队列：顺序播放多段音频 */
export function createAudioQueue(
  audioEl: HTMLAudioElement,
  onAllDone: () => void
) {
  const queue: string[] = [];
  let isPlaying = false;

  const playNext = () => {
    if (queue.length === 0) {
      isPlaying = false;
      onAllDone();
      return;
    }
    const url = queue.shift()!;
    audioEl.src = url;
    audioEl.onended = () => {
      URL.revokeObjectURL(url);
      playNext();
    };
    audioEl.play().catch(() => playNext());
  };

  return {
    enqueue(blob: Blob) {
      const url = URL.createObjectURL(blob);
      queue.push(url);
      if (!isPlaying) {
        isPlaying = true;
        playNext();
      }
    },
    /** 流结束时调用：若队列为空则立即触发 onAllDone */
    ensureIdle() {
      if (!isPlaying && queue.length === 0) {
        isPlaying = false;
        onAllDone();
      }
    },
  };
}
