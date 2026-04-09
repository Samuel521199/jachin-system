import { Buffer } from "node:buffer";

/**
 * Tauri updater 平台键：{target}-{arch}，与官方 guest 模板一致。
 * @see https://v2.tauri.app/plugin/updater/
 */
export function tauriPlatformKeyFromParts(target: string, arch: string): string {
  const t = target.trim().toLowerCase();
  let a = arch.trim().toLowerCase();
  if (a === "arm64") a = "aarch64";
  return `${t}-${a}`;
}

/**
 * Tauri updater / jachin-updater-helper：JSON 里 `signature` 须为「整份 .sig 字节的标准 Base64」单行。
 * - 已是该格式但带 MIME 折行 → 压成一行。
 * - 误存了明文 minisign → 再包一层 Base64（与 publish_desktop_release read_signature_text 一致）。
 * - 误「双重 Base64」（先 read_signature_text 又对整段 ASCII 再 base64）→ 剥掉外层，只发内层给客户端。
 */
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
    /* 保持 compact */
  }
  return compact;
}

/** 管理端入库前调用，避免 DB 中长期保存带折行的 signature。 */
export function normalizeArtifactSignatureForStorage(signature: string): string {
  return signatureWireFormatForTauri(signature);
}

// ---------------------------------------------------------------------------
// 签名内 file 字段与登记 version 一致（防「显示 0.8.75、实际为 0.8.74 安装包」）
// ---------------------------------------------------------------------------

function decodeMinisignPlaintextFromStoredSignature(stored: string): string | null {
  const t = stored.trim();
  if (t.startsWith("untrusted comment:")) {
    return t.replace(/\r\n/g, "\n");
  }
  const compact = t.replace(/\s+/g, "");
  if (!/^[A-Za-z0-9+/]+=*$/.test(compact) || compact.length < 24) {
    return null;
  }
  try {
    const once = Buffer.from(compact, "base64").toString("utf8");
    if (once.trimStart().startsWith("untrusted comment:")) {
      return once.replace(/\r\n/g, "\n");
    }
    const inner = once.replace(/\s+/g, "");
    if (/^[A-Za-z0-9+/]+=*$/.test(inner) && inner.length >= 24) {
      const twice = Buffer.from(inner, "base64").toString("utf8");
      if (twice.trimStart().startsWith("untrusted comment:")) {
        return twice.replace(/\r\n/g, "\n");
      }
    }
  } catch {
    return null;
  }
  return null;
}

function parseTrustedFileFieldFromMinisignPlaintext(plaintext: string): string | null {
  for (const line of plaintext.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed.startsWith("trusted comment:")) continue;
    const rest = trimmed.slice("trusted comment:".length).trim();
    const idx = rest.indexOf("file:");
    if (idx === -1) continue;
    const tail = rest.slice(idx + 5).trim();
    const name = (tail.split("\n")[0] ?? tail).trim();
    if (name.length > 0) return name;
  }
  return null;
}

function extractDottedSemvers(s: string): string[] {
  const out: string[] = [];
  for (const part of s.split(/[^0-9.]+/).filter(Boolean)) {
    const segs = part.split(".");
    if (
      segs.length === 3 &&
      segs.every((p) => p.length > 0 && /^\d+$/.test(p))
    ) {
      out.push(part);
    }
  }
  return out;
}

export type SignatureVersionCheckResult =
  | { ok: true }
  | { ok: false; message: string };

export type ValidateSignatureOptions = {
  /** 为 true 时：无法解码 signature 则不校验（兼容极旧入库格式）；管理端登记应传 false。 */
  allowUndecodable?: boolean;
};

/**
 * 校验 minisign trusted comment 里 `file:` 文件名中的 x.y.z 与登记的桌面版本一致。
 * 与客户端 jachin-updater-helper / updater_common 逻辑对齐。
 */
export function validateArtifactSignatureMatchesDeclaredVersion(
  signatureStored: string,
  declaredVersion: string,
  options?: ValidateSignatureOptions
): SignatureVersionCheckResult {
  const plaintext = decodeMinisignPlaintextFromStoredSignature(signatureStored.trim());
  if (!plaintext) {
    if (options?.allowUndecodable) {
      return { ok: true };
    }
    return {
      ok: false,
      message:
        "无法从 signature 解码出 minisign 明文（应为整份 .sig 的标准 Base64）。请检查发布脚本入库字段。",
    };
  }
  const signedName = parseTrustedFileFieldFromMinisignPlaintext(plaintext);
  if (!signedName) {
    return { ok: true };
  }
  const exp = declaredVersion.trim().replace(/^v/i, "").toLowerCase();
  const low = signedName.toLowerCase();
  if (low.includes(exp)) {
    return { ok: true };
  }
  const found = extractDottedSemvers(signedName);
  if (found.length === 0) {
    return { ok: true };
  }
  if (found.some((v) => v.toLowerCase() === exp)) {
    return { ok: true };
  }
  return {
    ok: false,
    message: `签名的 trusted file「${signedName}」中的版本号 (${found.join(", ")}) 与登记的 version「${declaredVersion}」不一致；请用正确版本的安装包重新签名并发布，勿只改版本号不上传新构建。`,
  };
}

/**
 * objectKey 路径中应出现与 version 一致的目录段（如 .../0.8.75/windows-x86_64/...）。
 */
export function validateObjectKeyContainsVersionSegment(
  objectKey: string,
  declaredVersion: string
): SignatureVersionCheckResult {
  const v = declaredVersion.trim().replace(/^v/i, "");
  if (!v) return { ok: true };
  const parts = objectKey.split("/").map((p) => p.trim()).filter(Boolean);
  if (parts.some((p) => p === v)) {
    return { ok: true };
  }
  return {
    ok: false,
    message: `objectKey「${objectKey}」的路径段中未找到与 version「${declaredVersion}」一致的目录（期望路径中含 /${v}/）。请检查发布脚本生成的对象键。`,
  };
}
