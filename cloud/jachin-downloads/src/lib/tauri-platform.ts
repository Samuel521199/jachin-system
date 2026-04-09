import { Buffer } from "node:buffer";

/** Tauri updater 平台键：{target}-{arch} */
export function tauriPlatformKeyFromParts(target: string, arch: string): string {
  const t = target.trim().toLowerCase();
  let a = arch.trim().toLowerCase();
  if (a === "arm64") a = "aarch64";
  return `${t}-${a}`;
}

/** 与 nexus desktop-releases-common.signatureWireFormatForTauri 一致 */
export function signatureWireFormatForTauri(signature: string): string {
  const t = signature.trim();
  if (!t) return t;
  const compact = t.replace(/\s+/g, "");
  if (!/^[A-Za-z0-9+/]+=*$/.test(compact) || compact.length < 24) {
    const normalized = t.replace(/\r\n/g, "\n");
    return Buffer.from(normalized, "utf8").toString("base64");
  }
  try {
    const once = Buffer.from(compact, "base64").toString("utf8");
    if (once.trimStart().startsWith("untrusted comment:")) {
      return compact;
    }
    const inner = once.replace(/\s+/g, "");
    if (/^[A-Za-z0-9+/]+=*$/.test(inner) && inner.length >= 24) {
      const twice = Buffer.from(inner, "base64").toString("utf8");
      if (twice.trimStart().startsWith("untrusted comment:")) {
        return inner;
      }
    }
  } catch {
    /* keep compact */
  }
  return compact;
}
