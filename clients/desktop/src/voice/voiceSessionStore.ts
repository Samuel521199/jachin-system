export type VoiceSessionState = "idle" | "listening" | "thinking" | "speaking" | "error";

type Listener = (state: VoiceSessionState) => void;

class VoiceSessionStore {
  private state: VoiceSessionState = "idle";
  private listeners = new Set<Listener>();

  getState(): VoiceSessionState {
    return this.state;
  }

  setState(next: VoiceSessionState): void {
    if (this.state === next) return;
    this.state = next;
    for (const l of this.listeners) l(this.state);
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    listener(this.state);
    return () => {
      this.listeners.delete(listener);
    };
  }
}

export const voiceSessionStore = new VoiceSessionStore();
