/**
 * Pairing store for Demo mode (when DATABASE_URL not configured).
 * 支持文件持久化，避免 Next.js dev 热重载清空内存导致配对码失效。
 */
import fs from "fs";

export interface PairingSession {
  session_id: string;
  short_code: string;
  status: "pending" | "approved" | "expired";
  expires_at: string;
  instance_id?: string;
  user_id?: string;
}

const store = new Map<string, PairingSession>();
const byCode = new Map<string, string>();

const PAIRING_FILE = `${process.cwd()}/.nexus-pairing.json`;

function normalizeCode(code: string): string {
  return code.trim().toUpperCase().replace(/[-_\s]/g, "");
}

function persistToFile(): void {
  try {
    const arr = Array.from(store.values());
    fs.writeFileSync(PAIRING_FILE, JSON.stringify(arr, null, 0), "utf8");
  } catch {
    /* ignore */
  }
}

function loadFromFile(): void {
  try {
    if (!fs.existsSync(PAIRING_FILE)) return;
    const raw = fs.readFileSync(PAIRING_FILE, "utf8");
    const arr = JSON.parse(raw) as PairingSession[];
    const now = new Date();
    for (const s of arr) {
      if (new Date(s.expires_at) >= now) {
        store.set(s.session_id, s);
        byCode.set(normalizeCode(s.short_code), s.session_id);
      }
    }
  } catch {
    /* ignore */
  }
}

export function pairingStoreSet(session: PairingSession): void {
  store.set(session.session_id, session);
  byCode.set(normalizeCode(session.short_code), session.session_id);
  persistToFile();
}

export function pairingStoreGetBySession(sessionId: string): PairingSession | undefined {
  let s = store.get(sessionId);
  if (!s) {
    loadFromFile();
    s = store.get(sessionId);
  }
  return s;
}

export function pairingStoreGetByCode(code: string): PairingSession | undefined {
  const key = normalizeCode(code);
  let sid = byCode.get(key);
  if (!sid) {
    loadFromFile();
    sid = byCode.get(key);
  }
  return sid ? store.get(sid) : undefined;
}

export function pairingStoreApprove(
  sessionId: string,
  instanceId: string,
  userId?: string
): void {
  const s = store.get(sessionId);
  if (s) {
    s.status = "approved";
    s.instance_id = instanceId;
    if (userId) s.user_id = userId;
    persistToFile();
  }
}

export function pairingStoreApproveByCode(
  code: string,
  instanceId: string,
  userId?: string
): boolean {
  const s = pairingStoreGetByCode(code);
  if (!s) return false;
  pairingStoreApprove(s.session_id, instanceId, userId);
  return true;
}

export function pairingStoreCleanup(): void {
  loadFromFile(); // 先加载文件，避免新请求覆盖已有有效会话
  const now = new Date();
  for (const [id, s] of store.entries()) {
    if (new Date(s.expires_at) < now) {
      store.delete(id);
      byCode.delete(normalizeCode(s.short_code));
    }
  }
  persistToFile();
}
