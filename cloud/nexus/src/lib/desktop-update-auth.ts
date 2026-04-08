import { timingSafeEqual } from "crypto";

/**
 * 与 ~/.jachin/nexus_config.json 中 `desktop_update_token` 一致，用于非 edge_agents 场景的桌面热更新鉴权。
 * 设置 NEXUS .env 的 DESKTOP_UPDATE_BEARER（建议 32+ 随机字符）。
 */
export function isDesktopUpdateSharedSecretBearer(bearer: string): boolean {
  const secret = process.env.DESKTOP_UPDATE_BEARER?.trim();
  if (!secret || secret.length < 8) return false;
  if (bearer.length !== secret.length) return false;
  try {
    return timingSafeEqual(Buffer.from(bearer, "utf8"), Buffer.from(secret, "utf8"));
  } catch {
    return false;
  }
}
