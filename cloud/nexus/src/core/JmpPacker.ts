/**
 * JmpPacker - Layer 1 武器锻造厂
 * 将插件 payload 打包为符合 JMP 规范的 .jmp 文件，并加盖 Ed25519 签名
 * 符合 docs/JMP_SPEC.md 与 docs/P0_TRUST_AND_HEARTBEAT_SPEC.md
 */

import crypto from "crypto";
import fs from "fs";
import path from "path";
import { createWriteStream } from "fs";
import archiver from "archiver";

export interface JmpManifest {
  jmp_version: string;
  plugin_id: string;
  version: string;
  name: string;
  entrypoint: string;
  permissions: string[];
  content_hashes: Record<string, string>;
  issued_at: string;
  [key: string]: unknown;
}

export class JmpPacker {
  private privateKey: crypto.KeyObject;

  constructor(privateKeyPem: string) {
    this.privateKey = crypto.createPrivateKey(privateKeyPem);
  }

  /**
   * 打包并签名，产出标准 .jmp 文件
   * @param pluginId 插件唯一标识
   * @param payloadDir payload 目录（含 module.wasm / main.py 等）
   * @param outputPath 输出 .jmp 文件路径
   * @param permissions 权限列表
   * @param options 可选覆盖：name, entrypoint, 及任意 JMP 扩展字段
   */
  public async pack(
    pluginId: string,
    payloadDir: string,
    outputPath: string,
    permissions: string[] = ["sandbox.execute"],
    options?: Partial<JmpManifest> & Record<string, unknown>
  ): Promise<void> {
    const payloadPath = path.resolve(payloadDir);
    if (!fs.existsSync(payloadPath)) {
      throw new Error(`Payload 目录不存在: ${payloadPath}`);
    }

    // 1. 计算 payload 下各文件的 SHA-256 哈希
    const contentHashes: Record<string, string> = {};
    const payloadFiles = this.walkPayloadFiles(payloadPath);

    for (const relPath of payloadFiles) {
      const fullPath = path.join(payloadPath, relPath);
      const buffer = fs.readFileSync(fullPath);
      const hash = crypto.createHash("sha256").update(buffer).digest("hex");
      contentHashes[`payload/${relPath}`] = hash;
    }

    // 2. 构建 manifest（规范化 JSON，键排序以保证签名一致性）
    const base: JmpManifest = {
      jmp_version: "2.0",
      plugin_id: pluginId,
      version: "1.0.0",
      name: pluginId,
      entrypoint: "module.wasm",
      permissions,
      content_hashes: contentHashes,
      issued_at: new Date().toISOString(),
    };
    const manifest: JmpManifest = options
      ? {
          ...base,
          ...options,
          plugin_id: pluginId,
          content_hashes: contentHashes,
          issued_at: base.issued_at,
        }
      : base;

    const manifestString = this.canonicalizeJson(manifest);

    // 3. Ed25519 签名 manifest
    const signature = crypto.sign(
      null,
      Buffer.from(manifestString, "utf8"),
      this.privateKey
    );
    const signatureBase64 = signature.toString("base64");

    // 4. 写入临时目录并打包为 ZIP
    const tmpDir = path.join(path.dirname(outputPath), `jmp-tmp-${Date.now()}`);
    fs.mkdirSync(tmpDir, { recursive: true });

    try {
      fs.writeFileSync(
        path.join(tmpDir, "manifest.json"),
        manifestString,
        "utf8"
      );
      fs.writeFileSync(
        path.join(tmpDir, "signature.sig"),
        signatureBase64,
        "utf8"
      );

      // 复制 payload 到 tmp/payload
      const tmpPayloadDir = path.join(tmpDir, "payload");
      fs.mkdirSync(tmpPayloadDir, { recursive: true });
      for (const relPath of payloadFiles) {
        const src = path.join(payloadPath, relPath);
        const dest = path.join(tmpPayloadDir, relPath);
        fs.mkdirSync(path.dirname(dest), { recursive: true });
        fs.copyFileSync(src, dest);
      }

      // 创建 .jmp (ZIP)
      await this.createZip(tmpDir, outputPath);
    } finally {
      fs.rmSync(tmpDir, { recursive: true, force: true });
    }
  }

  /** 递归收集 payload 目录下的文件（相对路径） */
  private walkPayloadFiles(dir: string, prefix = ""): string[] {
    const entries = fs.readdirSync(dir, { withFileTypes: true });
    const files: string[] = [];
    for (const e of entries) {
      const rel = prefix ? `${prefix}/${e.name}` : e.name;
      if (e.isDirectory()) {
        files.push(...this.walkPayloadFiles(path.join(dir, e.name), rel));
      } else {
        files.push(rel);
      }
    }
    return files;
  }

  /** 规范化 JSON：键排序、无空白，保证签名可复现 */
  private canonicalizeJson(obj: unknown): string {
    return JSON.stringify(this.sortKeys(obj));
  }

  private sortKeys(obj: unknown): unknown {
    if (obj === null || typeof obj !== "object") return obj;
    if (Array.isArray(obj)) return obj.map((v) => this.sortKeys(v));
    const sorted: Record<string, unknown> = {};
    for (const k of Object.keys(obj as Record<string, unknown>).sort()) {
      sorted[k] = this.sortKeys((obj as Record<string, unknown>)[k]);
    }
    return sorted;
  }

  /** 将目录打包为 ZIP */
  private async createZip(sourceDir: string, outputPath: string): Promise<void> {
    return new Promise((resolve, reject) => {
      const out = createWriteStream(outputPath);
      const archive = archiver("zip", { zlib: { level: 9 } });

      out.on("close", () => resolve());
      archive.on("error", reject);

      archive.pipe(out);

      archive.directory(sourceDir, false);
      archive.finalize();
    });
  }
}
