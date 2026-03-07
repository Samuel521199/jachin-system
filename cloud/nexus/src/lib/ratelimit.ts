/**
 * L1 API 高并发防护 — 速率限制
 *
 * 使用 in-memory 滑动窗口，单实例部署有效。
 * 多实例/分布式部署时，可切换为 @upstash/ratelimit + Redis。
 *
 * 限流策略：
 * - /api/v1/sync/manifest: 60 req/min per identifier
 * - /api/v1/telemetry/report: 30 req/min per identifier
 */

const WINDOW_MS = 60 * 1000; // 1 分钟

type Entry = { count: number; windowStart: number };

const _store = new Map<string, Entry>();

function _getIdentifier(request: Request): string {
  const forwarded = request.headers.get("x-forwarded-for");
  const realIp = request.headers.get("x-real-ip");
  const ip = (forwarded?.split(",")[0]?.trim() || realIp || "unknown").slice(0, 64);
  const tenant = request.headers.get("x-tenant-id")?.trim().slice(0, 64);
  return tenant ? `tenant:${tenant}` : `ip:${ip}`;
}

function _cleanup() {
  const now = Date.now();
  for (const [k, v] of _store.entries()) {
    if (now - v.windowStart > WINDOW_MS * 2) _store.delete(k);
  }
}

/**
 * 检查是否超过速率限制
 * @param identifier 限流标识（tenant 或 IP）
 * @param limit 窗口内最大请求数
 * @returns true 表示允许，false 表示超限
 */
export function checkRateLimit(identifier: string, limit: number): boolean {
  const now = Date.now();
  const entry = _store.get(identifier);

  if (!entry) {
    _store.set(identifier, { count: 1, windowStart: now });
    if (_store.size > 10000) _cleanup();
    return true;
  }

  if (now - entry.windowStart >= WINDOW_MS) {
    entry.count = 1;
    entry.windowStart = now;
    return true;
  }

  entry.count++;
  if (entry.count > limit) return false;
  return true;
}

/**
 * 从 Request 提取标识并检查限流
 */
export function rateLimit(request: Request, limit: number): { ok: boolean; identifier: string } {
  const identifier = _getIdentifier(request);
  const ok = checkRateLimit(identifier, limit);
  return { ok, identifier };
}

/** 预设：manifest 60/min */
export const MANIFEST_LIMIT = 60;

/** 预设：telemetry report 30/min */
export const TELEMETRY_LIMIT = 30;
