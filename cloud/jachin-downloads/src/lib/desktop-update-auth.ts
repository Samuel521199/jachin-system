import { timingSafeEqual } from "crypto";

/** 与 Nexus 相同：DESKTOP_UPDATE_BEARER + nexus_config desktop_update_token */
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
