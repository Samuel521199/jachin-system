/**
 * 工作区 slug：小写、数字与连字符，2～64 字符（与 DB varchar(64) 对齐）。
 */
const SLUG_RE = /^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/;

export function normalizeOrgSlugInput(raw: unknown): string | null {
  if (typeof raw !== "string") return null;
  const s = raw.trim().toLowerCase().replace(/\s+/g, "-");
  if (s.length < 2 || s.length > 64) return null;
  if (!SLUG_RE.test(s)) return null;
  return s;
}
