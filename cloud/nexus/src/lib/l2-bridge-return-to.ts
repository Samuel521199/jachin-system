/**
 * 校验 L2 绑定回跳 URL，防止 open redirect。
 * 环境变量 L2_BRIDGE_ALLOWED_RETURN_PREFIXES：逗号分隔前缀，如
 * http://47.86.39.173:18888,http://localhost:18888
 */
export function validateL2BridgeReturnTo(
  urlStr: string,
  prefixesRaw: string | undefined,
): boolean {
  const trimmed = urlStr.trim();
  if (!trimmed) return false;
  let u: URL;
  try {
    u = new URL(trimmed);
  } catch {
    return false;
  }
  if (u.protocol !== "http:" && u.protocol !== "https:") return false;

  const prefixes = (prefixesRaw || "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);

  if (prefixes.length === 0) {
    return (
      process.env.NODE_ENV !== "production" &&
      (u.hostname === "localhost" || u.hostname === "127.0.0.1")
    );
  }

  const normalized = trimmed.replace(/\/$/, "");
  return prefixes.some((p) => {
    const pre = p.replace(/\/$/, "");
    return normalized.startsWith(pre);
  });
}
