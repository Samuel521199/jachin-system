import { invoke } from "@tauri-apps/api/core";
import { voiceCompanionDebug } from "./voiceCompanionDebugLog";
import { voiceChatTraceIfActive } from "./voiceChatTraceLog";
import { stopNativeVoicePlayback } from "./voiceNativeBridge";

type AudioTask = {
  generation: number;
  blob: Blob;
};

function parseWavInfo(buf: ArrayBuffer): Record<string, number | string> {
  try {
    const view = new DataView(buf);
    const riff = String.fromCharCode(...new Uint8Array(buf.slice(0, 4)));
    const wave = String.fromCharCode(...new Uint8Array(buf.slice(8, 12)));
    if (riff !== "RIFF" || wave !== "WAVE") return { format: "unknown" };
    let offset = 12;
    let sampleRate = 0;
    let channels = 0;
    let bitsPerSample = 0;
    let dataBytes = 0;
    while (offset + 8 <= view.byteLength) {
      const id = String.fromCharCode(...new Uint8Array(buf.slice(offset, offset + 4)));
      const size = view.getUint32(offset + 4, true);
      const body = offset + 8;
      if (id === "fmt " && body + 16 <= view.byteLength) {
        channels = view.getUint16(body + 2, true);
        sampleRate = view.getUint32(body + 4, true);
        bitsPerSample = view.getUint16(body + 14, true);
      } else if (id === "data") {
        dataBytes = size;
        break;
      }
      offset = body + size + (size % 2);
    }
    const bytesPerSecond = sampleRate * channels * Math.max(1, bitsPerSample) / 8;
    const durationMs = bytesPerSecond > 0 ? Math.round((dataBytes / bytesPerSecond) * 1000) : 0;
    return { format: "wav", sampleRate, channels, bitsPerSample, dataBytes, durationMs };
  } catch (e) {
    return { format: "parse_failed", err: String(e) };
  }
}

async function blobToBase64(blob: Blob): Promise<{ base64: string; wavInfo: Record<string, number | string> }> {
  const buf = await blob.arrayBuffer();
  const bytes = new Uint8Array(buf);
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return { base64: btoa(binary), wavInfo: parseWavInfo(buf) };
}

export class VoicePlaybackController {
  private builtinAudio = new Audio();
  private hostAudio: HTMLAudioElement | null = null;
  private queue: AudioTask[] = [];
  private playing = false;
  private generation = 0;
  private isStopping = false;
  private autoplayPrimed = false;
  private idleWaiters: Array<() => void> = [];
  /** 陪伴态默认走 Rust 系统扬声器，WebView audio 作回退 */
  private preferNativePlayback = true;

  /** 绑定 chat 窗内 <audio>，有利于 WebView 自动播放策略 */
  setHostAudioElement(el: HTMLAudioElement | null): void {
    if (this.hostAudio === el) return;
    this.hostAudio = el;
    this.autoplayPrimed = false;
    voiceCompanionDebug("playback.host_audio", {
      bound: Boolean(el),
      using: el ? "host" : "builtin",
      preferNative: this.preferNativePlayback,
    });
  }

  private get audio(): HTMLAudioElement {
    return this.hostAudio ?? this.builtinAudio;
  }

  /** 尝试解锁自动播放（WebView 播放前调用；forceWeb=true 时可在 native 失败回退场景强制执行） */
  async primeAutoplay(opts?: { forceWeb?: boolean }): Promise<void> {
    const forceWeb = Boolean(opts?.forceWeb);
    if ((!forceWeb && this.preferNativePlayback) || this.autoplayPrimed || this.playing) return;
    const a = this.audio;
    try {
      a.muted = true;
      await a.play();
      a.pause();
      a.currentTime = 0;
      a.muted = false;
      this.autoplayPrimed = true;
      voiceCompanionDebug("playback.autoplay_primed", { ok: true, forceWeb });
    } catch (e) {
      voiceCompanionDebug("playback.autoplay_prime_fail", { err: String(e), forceWeb });
      console.warn("[VoicePlayback] autoplay prime failed (需先在桌面端点一次 Orb/聊天):", e);
    }
  }

  bumpGeneration(): number {
    this.generation += 1;
    voiceCompanionDebug("playback.bump_generation", { generation: this.generation });
    return this.generation;
  }

  getGeneration(): number {
    return this.generation;
  }

  clearQueue(): void {
    voiceCompanionDebug("playback.clear_queue", { had: this.queue.length });
    this.queue = [];
    this.resolveIdleIfDone();
  }

  async stopAndReset(): Promise<void> {
    this.generation += 1;
    this.queue = [];
    voiceCompanionDebug("playback.stop_invalidate", { generation: this.generation });
    this.resolveIdleIfDone();
    if (this.isStopping) return;
    this.isStopping = true;
    try {
      await stopNativeVoicePlayback();
      if (!this.preferNativePlayback) {
        await this.fadeOutWebAudio();
      }
      this.playing = false;
      this.resolveIdleIfDone();
    } finally {
      this.isStopping = false;
    }
  }

  /** 50～100ms 线性淡出，避免硬 pause 爆音 */
  private async fadeOutWebAudio(): Promise<void> {
    const a = this.audio;
    const steps = 5;
    const stepMs = 16;
    try {
      for (let i = steps; i >= 0; i -= 1) {
        a.volume = i / steps;
        await new Promise<void>((r) => setTimeout(r, stepMs));
      }
    } catch {
      // ignore
    }
    a.pause();
    a.currentTime = 0;
    a.onended = null;
    a.src = "";
    a.load();
    a.volume = 1;
  }

  async enqueue(blob: Blob, generation: number): Promise<void> {
    if (generation !== this.generation) {
      voiceCompanionDebug("playback.enqueue_skip_stale", {
        taskGen: generation,
        currentGen: this.generation,
        bytes: blob.size,
      });
      return;
    }
    voiceCompanionDebug("playback.enqueue", {
      bytes: blob.size,
      generation,
      queueLen: this.queue.length,
      playing: this.playing,
      native: this.preferNativePlayback,
    });
    voiceChatTraceIfActive("tts.playback_enqueue", {
      bytes: blob.size,
      generation,
      queueLen: this.queue.length,
      playing: this.playing,
      native: this.preferNativePlayback,
    });
    this.queue.push({ blob, generation });
    if (!this.playing) {
      void this.playLoop();
    }
  }

  waitForIdle(): Promise<void> {
    if (!this.playing && this.queue.length === 0) {
      return Promise.resolve();
    }
    return new Promise<void>((resolve) => {
      this.idleWaiters.push(resolve);
    });
  }

  private resolveIdleIfDone(): void {
    if (this.playing || this.queue.length > 0 || this.idleWaiters.length === 0) return;
    const waiters = this.idleWaiters.splice(0);
    for (const resolve of waiters) resolve();
  }

  private async playOneBlob(blob: Blob, generation: number): Promise<void> {
    if (this.preferNativePlayback) {
      try {
        const encodeStartedAt = Date.now();
        const { base64: wavBase64, wavInfo } = await blobToBase64(blob);
        voiceChatTraceIfActive("tts.playback_native_start", {
          bytes: blob.size,
          generation,
          encodeMs: Date.now() - encodeStartedAt,
          wavInfo,
        });
        voiceCompanionDebug("playback.native_start", { bytes: blob.size, generation, wavInfo });
        const playStartedAt = Date.now();
        await invoke("voice_companion_play_wav", { wavBase64 });
        voiceCompanionDebug("playback.native_ok", { generation });
        voiceChatTraceIfActive("tts.playback_native_done", {
          generation,
          latencyMs: Date.now() - playStartedAt,
        });
        voiceCompanionDebug("playback.path_result", { generation, play_path: "native" });
        return;
      } catch (e) {
        voiceCompanionDebug("playback.native_fail", { err: String(e), generation });
        voiceChatTraceIfActive("tts.playback_native_fail", { err: String(e), generation });
        console.warn("[VoicePlayback] native play failed, fallback to WebView audio:", e);
        // native 失败后会立即回退到 WebView；这里强制尝试解锁 autoplay，避免双重失败。
        await this.primeAutoplay({ forceWeb: true });
      }
    }

    const url = URL.createObjectURL(blob);
    const a = this.audio;
    try {
      a.pause();
      a.onended = null;
      a.src = url;
      const wavInfo = parseWavInfo(await blob.arrayBuffer());
      voiceCompanionDebug("playback.play_start", { bytes: blob.size, generation, wavInfo });
      voiceChatTraceIfActive("tts.playback_web_start", { bytes: blob.size, generation, wavInfo });
      try {
        await a.play();
        voiceCompanionDebug("playback.play_ok", { generation });
        voiceChatTraceIfActive("tts.playback_web_started", { generation });
      } catch (e) {
        voiceCompanionDebug("playback.play_fail", { err: String(e), generation });
        voiceChatTraceIfActive("tts.playback_web_fail", { err: String(e), generation });
        voiceCompanionDebug("playback.path_result", { generation, play_path: "none" });
        console.warn("[VoicePlayback] audio.play() failed:", e);
        return;
      }
      await new Promise<void>((resolve) => {
        a.onended = () => resolve();
      });
      voiceCompanionDebug("playback.play_ended", { generation });
      voiceChatTraceIfActive("tts.playback_web_ended", { generation });
      voiceCompanionDebug("playback.path_result", { generation, play_path: "web" });
    } finally {
      URL.revokeObjectURL(url);
    }
  }

  private async playLoop(): Promise<void> {
    this.playing = true;
    voiceCompanionDebug("playback.play_loop_start", { queue: this.queue.length });
    while (this.queue.length > 0) {
      const task = this.queue.shift();
      if (!task) break;
      if (task.generation !== this.generation) {
        voiceCompanionDebug("playback.play_skip_stale", {
          taskGen: task.generation,
          currentGen: this.generation,
        });
        continue;
      }
      await this.playOneBlob(task.blob, task.generation);
    }
    this.playing = false;
    voiceCompanionDebug("playback.play_loop_end", {});
    this.resolveIdleIfDone();
  }
}

export const voicePlaybackController = new VoicePlaybackController();
