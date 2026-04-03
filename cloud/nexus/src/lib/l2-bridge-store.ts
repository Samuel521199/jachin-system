/**
 * L2 网页绑定：一次性 bridge_code（mint 时写入，redeem 消费）。
 * 与 pairing-store 类似，支持无 DB 演示与文件持久化（dev 热重载）。
 */
import fs from "fs";
import { randomBytes } from "crypto";

export interface L2BridgeEntry {
  code: string;
  user_id: string;
  organization_id: string | null;
  email: string | null;
  expires_at: string;
}

const store = new Map<string, L2BridgeEntry>();
const BRIDGE_FILE = `${process.cwd()}/.nexus-l2-bridge.json`;

function persistToFile(): void {
  try {
    const arr = Array.from(store.values());
    fs.writeFileSync(BRIDGE_FILE, JSON.stringify(arr, null, 0), "utf8");
  } catch {
    /* ignore */
  }
}

function loadFromFile(): void {
  try {
    if (!fs.existsSync(BRIDGE_FILE)) return;
    const raw = fs.readFileSync(BRIDGE_FILE, "utf8");
    const arr = JSON.parse(raw) as L2BridgeEntry[];
    const now = new Date();
    for (const e of arr) {
      if (new Date(e.expires_at) >= now) {
        store.set(e.code, e);
      }
    }
  } catch {
    /* ignore */
  }
}

export function l2BridgeStoreSet(entry: L2BridgeEntry): void {
  store.set(entry.code, entry);
  persistToFile();
}

export function l2BridgeStoreTake(code: string): L2BridgeEntry | undefined {
  const c = code.trim().toLowerCase();
  let e = store.get(c);
  if (!e) {
    loadFromFile();
    e = store.get(c);
  }
  if (!e) return undefined;
  if (new Date(e.expires_at) < new Date()) {
    store.delete(c);
    persistToFile();
    return undefined;
  }
  store.delete(c);
  persistToFile();
  return e;
}

export function generateBridgeCode(): string {
  return randomBytes(24).toString("hex");
}
