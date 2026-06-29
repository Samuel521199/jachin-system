import { invoke } from "@tauri-apps/api/core";

export async function stopNativeVoicePlayback(): Promise<void> {
  try {
    await invoke("voice_companion_stop_playback");
  } catch {
    // non-tauri / command missing
  }
}

export async function notifyCompanionVoicePhase(
  phase: "idle" | "listening" | "thinking" | "speaking" | "error",
): Promise<void> {
  const mapped =
    phase === "error" ? "idle" : phase;
  try {
    await invoke("voice_companion_set_phase", { phase: mapped });
  } catch {
    // noop
  }
}

export async function previewWakeAckWav(id: string): Promise<void> {
  await invoke("voice_companion_play_wake_ack_preview", { id });
}
