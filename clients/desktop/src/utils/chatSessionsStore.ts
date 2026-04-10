/**
 * Omni 多会话：本地持久化（与 L3 `chat_id` / `session_id` 对齐）
 */
import type { StoredMessage } from "./messageStorage";
import { loadMessages } from "./messageStorage";

export interface ChatSession {
  id: string;
  title: string;
  messages: StoredMessage[];
  updatedAt: number;
}

const V2_KEY = "jachin_chat_sessions_v2";
const MAX_SESSIONS = 48;
const MAX_MESSAGES_PER_SESSION = 100;

export function titleFromMessages(messages: StoredMessage[]): string {
  const u = messages.find((m) => m.role === "user");
  if (!u?.content?.trim()) return "新对话";
  const t = u.content
    .trim()
    .replace(/^\[Lark\]\s*/i, "")
    .replace(/^[\[【].*?[】\]]\s*/, "")
    .slice(0, 44);
  return t.trim() || "新对话";
}

/** 侧栏列表展示：有正式标题用标题，否则用首条用户消息前 10 字 */
export function sessionSidebarDisplayLabel(s: ChatSession): string {
  const t = (s.title ?? "").trim();
  if (t && t !== "新对话") return t.length > 40 ? `${t.slice(0, 40)}…` : t;
  const u = s.messages.find((m) => m.role === "user");
  if (!u?.content?.trim()) return "新对话";
  const raw = u.content
    .trim()
    .replace(/^\[Lark\]\s*/i, "")
    .replace(/^[\[【].*?[】\]]\s*/, "");
  const ten = raw.slice(0, 10);
  return ten || "新对话";
}

function trimSessionMessages(msgs: StoredMessage[]): StoredMessage[] {
  if (msgs.length <= MAX_MESSAGES_PER_SESSION) return msgs;
  return msgs.slice(-MAX_MESSAGES_PER_SESSION);
}

function trimSessionsList(sessions: ChatSession[]): ChatSession[] {
  if (sessions.length <= MAX_SESSIONS) return sessions;
  const sorted = [...sessions].sort((a, b) => b.updatedAt - a.updatedAt);
  return sorted.slice(0, MAX_SESSIONS);
}

export interface LoadedSessionsState {
  sessions: ChatSession[];
  currentId: string;
}

export function loadSessionsState(): LoadedSessionsState {
  try {
    const raw = localStorage.getItem(V2_KEY);
    if (raw) {
      const j = JSON.parse(raw) as { currentId?: string; sessions?: unknown };
      const sessionsIn = Array.isArray(j.sessions) ? j.sessions : [];
      const sessions: ChatSession[] = sessionsIn
        .map((s: unknown) => {
          if (!s || typeof s !== "object") return null;
          const o = s as Record<string, unknown>;
          const id = typeof o.id === "string" ? o.id : "";
          const title = typeof o.title === "string" ? o.title : "新对话";
          const messages = Array.isArray(o.messages) ? (o.messages as StoredMessage[]) : [];
          const updatedAt = typeof o.updatedAt === "number" ? o.updatedAt : Date.now();
          if (!id) return null;
          return {
            id,
            title,
            messages: trimSessionMessages(
              messages.filter(
                (msg) =>
                  msg &&
                  typeof msg.role === "string" &&
                  typeof msg.content === "string" &&
                  typeof msg.timestamp === "number",
              ),
            ),
            updatedAt,
          };
        })
        .filter((x): x is ChatSession => x != null);
      if (sessions.length > 0) {
        let currentId = typeof j.currentId === "string" ? j.currentId : sessions[0].id;
        if (!sessions.some((s) => s.id === currentId)) currentId = sessions[0].id;
        return { sessions: trimSessionsList(sessions), currentId };
      }
    }
  } catch {
    /* fallthrough */
  }

  const legacy = loadMessages();
  if (legacy.length > 0) {
    const id = crypto.randomUUID();
    return {
      sessions: trimSessionsList([
        {
          id,
          title: titleFromMessages(legacy),
          messages: trimSessionMessages(legacy),
          updatedAt: Date.now(),
        },
      ]),
      currentId: id,
    };
  }

  const id = crypto.randomUUID();
  return {
    sessions: [{ id, title: "新对话", messages: [], updatedAt: Date.now() }],
    currentId: id,
  };
}

export function persistSessionsState(sessions: ChatSession[], currentId: string): void {
  try {
    const trimmed = trimSessionsList(
      sessions.map((s) => ({
        ...s,
        messages: trimSessionMessages(s.messages),
      })),
    );
    let cid = currentId;
    if (!trimmed.some((s) => s.id === cid) && trimmed.length > 0) cid = trimmed[0].id;
    localStorage.setItem(V2_KEY, JSON.stringify({ currentId: cid, sessions: trimmed }));
  } catch (e) {
    console.error("[ChatSessions] persist failed:", e);
  }
}

export function newEmptySession(): ChatSession {
  return {
    id: crypto.randomUUID(),
    title: "新对话",
    messages: [],
    updatedAt: Date.now(),
  };
}
