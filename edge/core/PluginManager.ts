/**
 * PluginManager - Layer 2 前线装卸工
 * 解包 .jmp 并进行极其严格的安全校验：Ed25519 签名 + content_hashes 防篡改
 * 符合 docs/P0_TRUST_AND_HEARTBEAT_SPEC.md
 */

import crypto from "crypto";
import fs from "fs";
import path from "path";
import * as unzipper from "unzipper";

export interface JmpManifest {
  plugin_id: string;
  version: string;
  name: string;
  entrypoint?: string;
  permissions: string[];
  content_hashes: Record<string, string>;
  issued_at?: string;
  [key: string]: unknown;
}

export class PluginManager {
  private layer1PublicKey: crypto.KeyObject;

  constructor(publicKeyPem: string) {
    this.layer1PublicKey = crypto.createPublicKey(publicKeyPem);
  }

  /**
   * 解包并进行极其严格的安全校验
   * @param jmpFilePath .jmp 文件路径（ZIP 格式）
   * @param extractDir 解压目标目录
   * @returns 校验通过后的 manifest
   */
  public async verifyAndLoad(
    jmpFilePath: string,
    extractDir: string
  ): Promise<JmpManifest> {
    console.log("📦 开始解析新武器...");

    // 1. 物理层解压
    await this.extractZip(jmpFilePath, extractDir);

    const manifestPath = path.join(extractDir, "manifest.json");
    const signaturePath = path.join(extractDir, "signature.sig");
    const payloadDir = path.join(extractDir, "payload");

    if (!fs.existsSync(manifestPath) || !fs.existsSync(signaturePath)) {
      throw new Error(
        "🚨 安全拦截：.jmp 包缺少 manifest.json 或 signature.sig"
      );
    }

    const manifestString = fs.readFileSync(manifestPath, "utf8");
    const signatureBase64 = fs.readFileSync(signaturePath, "utf8");

    // ==========================================
    // 🛡️ 防线一：Ed25519 签名真伪校验
    // ==========================================
    const isSignatureValid = crypto.verify(
      null,
      Buffer.from(manifestString, "utf8"),
      this.layer1PublicKey,
      Buffer.from(signatureBase64, "base64")
    );

    if (!isSignatureValid) {
      throw new Error(
        "🚨 安全拦截：签名校验失败！系统拒绝加载该模块。"
      );
    }
    console.log("✅ 签名校验通过 (来源可靠)");

    const manifest = JSON.parse(manifestString) as JmpManifest;
    const expectedHashes = manifest.content_hashes;

    if (!expectedHashes || typeof expectedHashes !== "object") {
      throw new Error(
        "🚨 安全拦截：manifest 缺少 content_hashes，无法进行完整性校验"
      );
    }

    // ==========================================
    // 🛡️ 防线二：Payload 资产防篡改校验 (哈希比对)
    // ==========================================
    if (!fs.existsSync(payloadDir)) {
      throw new Error("🚨 安全拦截：.jmp 包缺少 payload 目录");
    }

    for (const [relPath, expectedHash] of Object.entries(expectedHashes)) {
      const filePath = path.join(extractDir, relPath);
      if (!fs.existsSync(filePath)) {
        throw new Error(
          `🚨 安全拦截：manifest 声明的文件 [${relPath}] 不存在`
        );
      }
      const fileBuffer = fs.readFileSync(filePath);
      const actualHash = crypto
        .createHash("sha256")
        .update(fileBuffer)
        .digest("hex");
      const expected = String(expectedHash).replace(/^sha256:/i, "");

      if (actualHash !== expected) {
        throw new Error(
          `🚨 安全拦截：文件 [${relPath}] 哈希不匹配！代码已被污染。`
        );
      }
    }
    console.log("✅ 资产完整性校验通过 (代码未污染)");

    console.log(
      `🚀 武器 [${manifest.plugin_id}] 已准备就绪，正在推入执行沙箱...`
    );
    return manifest;
  }

  /** 解压 .jmp (ZIP) 到目标目录 */
  private async extractZip(zipPath: string, extractDir: string): Promise<void> {
    fs.mkdirSync(extractDir, { recursive: true });
    const directory = await unzipper.Open.file(zipPath);
    await directory.extract({ path: extractDir });
  }
}
