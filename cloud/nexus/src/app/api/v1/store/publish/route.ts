import { NextRequest, NextResponse } from "next/server";
import path from "path";
import fs from "fs";
import crypto from "crypto";
import AdmZip from "adm-zip";
import { getDb, isDatabaseConfigured } from "@/db";
import { pluginsRegistry } from "@/db/schema";
import { eq, and } from "drizzle-orm";
import { isIpfsConfigured, uploadToIpfs } from "@/lib/ipfs";

export const dynamic = "force-dynamic";

/** 语义化版本正则 */
const SEMVER_REGEX = /^\d+\.\d+\.\d+(-[a-zA-Z0-9.-]+)?(\+[a-zA-Z0-9.-]+)?$/;

/** plugin.json 结构（从 zip 内解析） */
interface PluginJson {
  id: string;
  name: string;
  version?: string;
  description?: string;
  item_type?: "SKILL" | "MCP";
  type?: string;
  runtime_tier?: "L3_LOCAL" | "L2_GATEWAY" | "L1_CLOUD";
  required_mcps?: string[];
}

/**
 * 从 Authorization: Bearer <token> 解析并校验，返回 developer_id
 */
function resolveDeveloperFromToken(authHeader: string | null): string | null {
  if (!authHeader?.startsWith("Bearer ")) return null;
  const token = authHeader.slice(7).trim();
  const expected = process.env.JACHIN_DEV_TOKEN;
  const devId = process.env.JACHIN_DEV_ID;
  if (!expected || !devId || token !== expected) return null;
  return devId;
}

/**
 * 解析 multipart 中的 zip 并校验 plugin.json
 */
function parseAndValidateZip(zipBuffer: Buffer): {
  pluginJson: PluginJson;
  raw: unknown;
} {
  let zip: AdmZip;
  try {
    zip = new AdmZip(zipBuffer);
  } catch {
    throw new PublishError(
      400,
      "INVALID_ZIP",
      "上传的包体不是有效的 ZIP 文件，请检查文件是否损坏。建议：使用 jachin-cli pack 重新打包。"
    );
  }

  const entries = zip.getEntries();
  const pluginEntry = entries.find(
    (e) => e.entryName === "plugin.json" || e.entryName.endsWith("/plugin.json")
  );

  if (!pluginEntry || pluginEntry.isDirectory) {
    throw new PublishError(
      400,
      "MISSING_PLUGIN_JSON",
      "包内必须包含 plugin.json。请确保插件根目录下有 plugin.json 文件。"
    );
  }

  let raw: unknown;
  try {
    const content = pluginEntry.getData().toString("utf8");
    raw = JSON.parse(content) as unknown;
  } catch {
    throw new PublishError(
      400,
      "INVALID_PLUGIN_JSON",
      "plugin.json 格式错误，无法解析为 JSON。请检查文件编码和语法。"
    );
  }

  const p = raw as Record<string, unknown>;
  const id = typeof p.id === "string" ? p.id.trim() : "";
  const name = typeof p.name === "string" ? p.name.trim() : "";
  const version = typeof p.version === "string" ? p.version.trim() : "1.0.0";

  if (!id || !/^[a-z0-9][a-z0-9.-]*[a-z0-9]$/i.test(id)) {
    throw new PublishError(
      400,
      "INVALID_PLUGIN_ID",
      "plugin.json 的 id 必须为非空字符串，建议使用反向域名格式（如 com.example.my-skill）。"
    );
  }

  if (!name) {
    throw new PublishError(
      400,
      "INVALID_PLUGIN_NAME",
      "plugin.json 的 name 不能为空。"
    );
  }

  if (!SEMVER_REGEX.test(version)) {
    throw new PublishError(
      400,
      "INVALID_VERSION",
      `版本号必须符合语义化版本规范（如 1.0.0），当前值: ${version}。`
    );
  }

  const itemType = (p.item_type ?? p.type ?? "skill") as string;
  const itemTypeNorm =
    String(itemType).toUpperCase() === "MCP" ? "MCP" : "SKILL";

  const runtimeTier = (p.runtime_tier ?? "L3_LOCAL") as string;
  const runtimeTierNorm =
    runtimeTier === "L2_GATEWAY"
      ? "L2_GATEWAY"
      : runtimeTier === "L1_CLOUD"
        ? "L1_CLOUD"
        : "L3_LOCAL";

  const requiredMcps = Array.isArray(p.required_mcps)
    ? (p.required_mcps as string[]).filter((x) => typeof x === "string")
    : [];

  const pluginJson: PluginJson = {
    id,
    name,
    version,
    description: typeof p.description === "string" ? p.description : undefined,
    item_type: itemTypeNorm,
    runtime_tier: runtimeTierNorm,
    required_mcps: requiredMcps,
  };

  // 076: 若包内含 config/ 则必须含 manifest.yaml
  validateConfigInZip(zip);

  return { pluginJson, raw };
}

/**
 * 076 规范：若 zip 内含 config/ 或 payload/config/，则必须含 manifest.yaml
 * 文档: docs/SKILL_MCP_UPLOAD_SPEC.md
 */
function validateConfigInZip(zip: AdmZip): void {
  const entries = zip.getEntries();
  const hasConfigDir = entries.some(
    (e) =>
      !e.isDirectory &&
      (e.entryName.startsWith("config/") || e.entryName.startsWith("payload/config/"))
  );
  if (!hasConfigDir) return;

  const hasManifest = entries.some(
    (e) =>
      !e.isDirectory &&
      (e.entryName === "config/manifest.yaml" ||
        e.entryName === "payload/config/manifest.yaml")
  );

  if (!hasManifest) {
    throw new PublishError(
      400,
      "MISSING_CONFIG_MANIFEST",
      "包内含 config 目录但缺少 config/manifest.yaml。依赖配置的 Skill/MCP 必须随包提供配置模板。规范: docs/SKILL_MCP_UPLOAD_SPEC.md"
    );
  }
}

/**
 * 077 规范：Skill 依赖的 MCP 必须已发布且审核通过
 * 文档: .cursor/rules/077-skill-mcp-dependency.mdc
 */
async function validateRequiredMcpsForSkill(
  db: ReturnType<typeof getDb>,
  requiredMcps: string[]
): Promise<void> {
  if (!requiredMcps || requiredMcps.length === 0) return;

  const pluginIdsLower = new Set(
    requiredMcps
      .filter((x) => typeof x === "string" && x.trim())
      .map((x) => x.replace(/^mcp:/i, "").trim().toLowerCase())
      .filter((x) => x)
  );

  if (pluginIdsLower.size === 0) return;

  const mcps = await db
    .select({
      pluginId: pluginsRegistry.pluginId,
      status: pluginsRegistry.status,
    })
    .from(pluginsRegistry)
    .where(and(eq(pluginsRegistry.itemType, "MCP")));

  const foundLower = new Set<string>();
  const notApproved: string[] = [];
  for (const m of mcps) {
    const pid = (m.pluginId ?? "").toLowerCase();
    if (pluginIdsLower.has(pid)) {
      foundLower.add(pid);
      if (m.status !== "approved") {
        notApproved.push(m.pluginId ?? pid);
      }
    }
  }

  for (const pid of pluginIdsLower) {
    if (!foundLower.has(pid)) {
      throw new PublishError(
        400,
        "REQUIRED_MCP_NOT_FOUND",
        `Skill 依赖的 MCP 必须先发布：mcp:${pid} 未在 L1 注册。请先发布该 MCP 后再发布 Skill。规范: .cursor/rules/077-skill-mcp-dependency.mdc`
      );
    }
  }

  if (notApproved.length > 0) {
    throw new PublishError(
      400,
      "REQUIRED_MCP_NOT_APPROVED",
      `Skill 依赖的 MCP 必须已审核通过：${notApproved.join(", ")} 尚未审核。请先在 L1 管理后台审核通过后再发布 Skill。规范: .cursor/rules/077-skill-mcp-dependency.mdc`
    );
  }
}

class PublishError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string
  ) {
    super(message);
    this.name = "PublishError";
  }
}

/**
 * 保存 zip 到本地存储，返回可访问的 package_url
 */
async function savePackageLocally(
  zipBuffer: Buffer,
  pluginId: string,
  version: string
): Promise<string> {
  const packagesDir = path.join(process.cwd(), "public", "packages");
  fs.mkdirSync(packagesDir, { recursive: true });
  const safeId = pluginId.replace(/\./g, "-");
  const safeVersion = version.replace(/[^a-zA-Z0-9.-]/g, "_");
  const fileName = `${safeId}_v${safeVersion}_${Date.now()}.zip`;
  const destPath = path.join(packagesDir, fileName);
  fs.writeFileSync(destPath, zipBuffer);
  const baseUrl =
    process.env.NEXUS_PUBLIC_URL ||
    process.env.VERCEL_URL
      ? `https://${process.env.VERCEL_URL}`
      : "https://nexus.jachin";
  return `${baseUrl.replace(/\/$/, "")}/packages/${fileName}`;
}

/**
 * 从 JSON 元数据校验并构建 PluginJson（影子上传用）
 */
function validateMetadataFromForm(
  formData: FormData
): { pluginJson: PluginJson; manifestJsonRaw: Record<string, unknown> } {
  const id = (formData.get("plugin_id") as string | null)?.trim() ?? "";
  const name = (formData.get("name") as string | null)?.trim() ?? "";
  const version = (formData.get("version") as string | null)?.trim() ?? "1.0.0";
  const description = (formData.get("description") as string | null)?.trim() ?? null;
  const itemTypeRaw = (formData.get("item_type") as string | null) ?? "SKILL";
  const itemTypeNorm = String(itemTypeRaw).toUpperCase() === "MCP" ? "MCP" : "SKILL";

  if (!id || !/^[a-z0-9][a-z0-9.-]*[a-z0-9]$/i.test(id)) {
    throw new PublishError(
      400,
      "INVALID_PLUGIN_ID",
      "plugin_id 必须为非空字符串，建议反向域名格式（如 com.example.my-skill）。"
    );
  }
  if (!name) {
    throw new PublishError(400, "INVALID_PLUGIN_NAME", "name 不能为空。");
  }
  if (!SEMVER_REGEX.test(version)) {
    throw new PublishError(400, "INVALID_VERSION", `版本号须符合语义化版本，当前: ${version}。`);
  }

  const pluginJson: PluginJson = {
    id,
    name,
    version,
    description: description || undefined,
    item_type: itemTypeNorm,
    runtime_tier: "L3_LOCAL",
    required_mcps: [],
  };
  const manifestJsonRaw: Record<string, unknown> = {
    id,
    name,
    version,
    description: description ?? "",
    item_type: itemTypeNorm,
    type: itemTypeNorm,
  };
  return { pluginJson, manifestJsonRaw };
}

/**
 * POST /api/v1/store/publish
 * 开发者发布中心 API — jachin-cli 上云发布
 *
 * - 鉴权：Authorization: Bearer <JACHIN_DEV_TOKEN>
 * - 请求体：multipart/form-data
 *   - package / package_file: .zip 包（shadow_only 时可选）
 *   - shadow_only: "true" 时仅登记元数据，不传包（PRIVATE 影子上传）
 *   - visibility, price_monthly 等商业参数（可选）
 */
export async function POST(request: NextRequest) {
  try {
    const authHeader = request.headers.get("Authorization");
    const developerId = resolveDeveloperFromToken(authHeader);
    if (!developerId) {
      return NextResponse.json(
        { success: false, error: "Unauthorized", code: "INVALID_TOKEN" },
        { status: 401 }
      );
    }

    const formData = await request.formData();
    const packageFile =
      (formData.get("package") as File | null) ??
      (formData.get("package_file") as File | null);
    const visibility = (formData.get("visibility") as string | null) ?? "PRIVATE";
    const priceMonthlyStr = (formData.get("price_monthly") as string | null) ?? "0";
    const shadowOnlyRaw = (formData.get("shadow_only") as string | null) ?? "false";
    const shadowOnly = shadowOnlyRaw === "true" || shadowOnlyRaw === "1";

    const priceMonthly = Math.max(0, parseInt(priceMonthlyStr, 10) || 0);
    const visibilityNorm: "PUBLIC" | "PRIVATE" =
      String(visibility).toUpperCase() === "PUBLIC" ? "PUBLIC" : "PRIVATE";

    let pluginJson: PluginJson;
    let manifestJsonRaw: Record<string, unknown>;
    let packageUrl: string | null = null;
    let packageSha256: string | null = null;

    if (shadowOnly) {
      if (visibilityNorm !== "PRIVATE") {
        return NextResponse.json(
          {
            success: false,
            error: "shadow_only 仅适用于 visibility=PRIVATE",
            code: "INVALID_SHADOW",
          },
          { status: 400 }
        );
      }
      try {
        const validated = validateMetadataFromForm(formData);
        pluginJson = validated.pluginJson;
        manifestJsonRaw = validated.manifestJsonRaw;
      } catch (err) {
        if (err instanceof PublishError) {
          return NextResponse.json(
            { success: false, error: err.message, code: err.code },
            { status: err.status }
          );
        }
        throw err;
      }
    } else {
      if (!packageFile || !(packageFile instanceof File)) {
        return NextResponse.json(
          {
            success: false,
            error: "缺少 package 或 package_file 字段，必须上传 .zip 包",
            code: "MISSING_PACKAGE",
          },
          { status: 400 }
        );
      }

      let zipBuffer: Buffer;
      try {
        zipBuffer = Buffer.from(await packageFile.arrayBuffer());
      } catch {
        return NextResponse.json(
          {
            success: false,
            error: "无法读取上传文件，请检查网络或重试",
            code: "READ_ERROR",
          },
          { status: 400 }
        );
      }

      if (zipBuffer.length === 0) {
        return NextResponse.json(
          {
            success: false,
            error: "上传的包体为空",
            code: "EMPTY_PACKAGE",
          },
          { status: 400 }
        );
      }

      try {
        const parsed = parseAndValidateZip(zipBuffer);
        pluginJson = parsed.pluginJson;
        manifestJsonRaw = (parsed.raw as Record<string, unknown>) ?? {};
      } catch (err) {
        if (err instanceof PublishError) {
          return NextResponse.json(
            { success: false, error: err.message, code: err.code },
            { status: err.status }
          );
        }
        throw err;
      }

      if (isIpfsConfigured()) {
        const ipfsResult = await uploadToIpfs(
          zipBuffer,
          `${pluginJson.id.replace(/\./g, "-")}_v${pluginJson.version ?? "1.0.0"}_${Date.now()}.zip`,
          "application/zip"
        );
        packageUrl = ipfsResult?.url ?? "";
      }
      if (!packageUrl) {
        packageUrl = await savePackageLocally(
          zipBuffer,
          pluginJson.id,
          pluginJson.version ?? "1.0.0"
        );
      }
      packageSha256 = crypto.createHash("sha256").update(zipBuffer).digest("hex");
    }

    if (!isDatabaseConfigured()) {
      return NextResponse.json({
        success: true,
        message: shadowOnly ? "影子上传接收成功（数据库未配置）" : "发布接收成功（数据库未配置，未入库）",
        plugin_id: pluginJson.id,
        name: pluginJson.name,
        version: pluginJson.version,
        package_url: packageUrl ?? undefined,
      });
    }

    const db = getDb()!;

    // 077 规范：Skill 依赖的 MCP 必须已发布且审核通过
    if (pluginJson.item_type === "SKILL" && (pluginJson.required_mcps?.length ?? 0) > 0) {
      await validateRequiredMcpsForSkill(db, pluginJson.required_mcps);
    }

    const existing = await db
      .select({ id: pluginsRegistry.id })
      .from(pluginsRegistry)
      .where(eq(pluginsRegistry.pluginId, pluginJson.id))
      .limit(1);

    const row = {
      pluginId: pluginJson.id,
      version: pluginJson.version ?? "1.0.0",
      itemType: (pluginJson.item_type ?? "SKILL") as "SKILL" | "MCP",
      name: pluginJson.name,
      description: pluginJson.description ?? null,
      developerId,
      visibility: visibilityNorm,
      priceMonthly,
      runtimeTier: (pluginJson.runtime_tier ?? "L3_LOCAL") as
        | "L3_LOCAL"
        | "L2_GATEWAY"
        | "L1_CLOUD",
      requiredMcps: pluginJson.required_mcps ?? [],
      packageUrl: packageUrl ?? null,
      packageSha256: packageSha256 ?? null,
      manifestJson: manifestJsonRaw,
      category: "skill",
      status: shadowOnly || process.env.NEXUS_AUTO_APPROVE === "1" ? "approved" : "pending",
      updatedAt: new Date(),
    };

    if (existing.length > 0) {
      await db
        .update(pluginsRegistry)
        .set(row)
        .where(eq(pluginsRegistry.pluginId, pluginJson.id));
    } else {
      await db.insert(pluginsRegistry).values(row);
    }

    return NextResponse.json({
      success: true,
      message: shadowOnly ? "影子上传成功，私有包已登记，实体请侧载到 L2" : "发布成功",
      plugin_id: pluginJson.id,
      name: pluginJson.name,
      version: pluginJson.version,
      package_url: packageUrl ?? undefined,
    });
  } catch (err) {
    console.error("[store/publish] Error:", err);
    return NextResponse.json(
      {
        success: false,
        error:
          err instanceof Error ? err.message : "Internal server error",
        code: "INTERNAL_ERROR",
      },
      { status: 500 }
    );
  }
}
