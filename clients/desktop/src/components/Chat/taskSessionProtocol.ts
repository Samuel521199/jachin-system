import type { StoredMessage } from "../../utils/messageStorage";

export type TaskSessionControl = NonNullable<StoredMessage["task_session"]>;

const TASK_SESSION_PROTOCOL_RE =
  /<!--\s*jachin-ui:task-session\s+({[\s\S]*?})\s*-->/i;

export function extractTaskSessionProtocol(body: string): TaskSessionControl | null {
  const match = TASK_SESSION_PROTOCOL_RE.exec(body || "");
  if (!match?.[1]) return null;
  try {
    const parsed = JSON.parse(match[1]) as TaskSessionControl & { type?: string };
    if ((parsed.type || "task_session") !== "task_session") return null;
    return parsed;
  } catch {
    return null;
  }
}

export function stripTaskSessionProtocol(body: string): string {
  return (body || "")
    .replace(TASK_SESSION_PROTOCOL_RE, "")
    .replace(/<!--\s*jachin-ui:task-session[\s\S]*$/i, "")
    .trimEnd();
}
