import type { StoredMessage } from "../../utils/messageStorage";

export type VoiceRuntimeControl = NonNullable<StoredMessage["voice_runtime"]>;

const VOICE_RUNTIME_PROTOCOL_RE =
  /<!--\s*jachin-ui:voice-runtime\s+([\s\S]*?)\s*-->/i;

export function extractVoiceRuntimeProtocol(body: string): VoiceRuntimeControl | null {
  const match = VOICE_RUNTIME_PROTOCOL_RE.exec(body || "");
  if (!match?.[1]) return null;
  try {
    const parsed = JSON.parse(match[1]) as VoiceRuntimeControl & { type?: string };
    if ((parsed.type || "voice_runtime") !== "voice_runtime") return null;
    return parsed;
  } catch {
    return null;
  }
}

export function stripVoiceRuntimeProtocol(body: string): string {
  return (body || "")
    .replace(VOICE_RUNTIME_PROTOCOL_RE, "")
    .replace(/<!--\s*jachin-ui:voice-runtime[\s\S]*$/i, "")
    .trimEnd();
}
