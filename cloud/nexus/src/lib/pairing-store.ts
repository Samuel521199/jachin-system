/**
 * In-memory pairing store for Demo mode (when Supabase not configured).
 * Enables full request → confirm → status flow without DB.
 */
export interface PairingSession {
  session_id: string;
  short_code: string;
  status: "pending" | "approved" | "expired";
  expires_at: string;
  instance_id?: string;
}

const store = new Map<string, PairingSession>();
const byCode = new Map<string, string>(); // short_code -> session_id

export function pairingStoreSet(session: PairingSession): void {
  store.set(session.session_id, session);
  byCode.set(session.short_code.toUpperCase(), session.session_id);
}

export function pairingStoreGetBySession(sessionId: string): PairingSession | undefined {
  return store.get(sessionId);
}

export function pairingStoreGetByCode(code: string): PairingSession | undefined {
  const sid = byCode.get(code.trim().toUpperCase());
  return sid ? store.get(sid) : undefined;
}

export function pairingStoreApprove(sessionId: string, instanceId: string): void {
  const s = store.get(sessionId);
  if (s) {
    s.status = "approved";
    s.instance_id = instanceId;
  }
}

export function pairingStoreApproveByCode(code: string, instanceId: string): boolean {
  const s = pairingStoreGetByCode(code);
  if (!s) return false;
  pairingStoreApprove(s.session_id, instanceId);
  return true;
}

export function pairingStoreCleanup(): void {
  const now = new Date();
  for (const [id, s] of store.entries()) {
    if (new Date(s.expires_at) < now) {
      store.delete(id);
      byCode.delete(s.short_code);
    }
  }
}
