import { NextRequest, NextResponse } from "next/server";
import path from "path";
import fs from "fs";
import crypto from "crypto";
import AdmZip from "adm-zip";
import { getDb, isDatabaseConfigured } from "@/db";
import { pluginsRegistry } from "@/db/schema";
import { eq, and } from "drizzle-orm";
import { isIpfsConfigured, uploadToIpfs } from "@/lib/ipfs";
import { appendL1DebugLine, appendL1DebugError } from "@/lib/l1-debug-file-log";

export const dynamic = "force-dynamic";

/** 语义化版本正则 */
const SEMVER_REGEX = /^\d+\.\d+\.\d+(-[a-zA-Z0-9.-]+)?(\+[a-zA-Z0-9.-]+)?$/;

function readLocalEnvValue(key: string): string | undefined {
  const fromProcess = process.env[key]?.trim();
  if (fromProcess) return fromProcess;

  try {
    const envPath = path.join(process.cwd(), ".env.local");
    if (!fs.existsSync(envPath)) return undefined;
    const prefix = `${key}=`;
    const lines = fs.readFileSync(envPath, "utf8").split(/\r?\n/);
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("#") || !trimmed.startsWith(prefix)) {
        continue;
      }
      return trimmed
        .slice(prefix.length)
        .trim()
        .replace(/^['"]|['"]$/g, "");
    }
  } catch {
    return undefined;
  }
  return undefined;
}

/** plugin.json 结构（从 zip 内解析） */
interface PluginJson {
  id: string;
  name: string;
  version?: string;
  description?: string;
  item_type?: "SKILL" | "MCP" | "TOOL" | "MODEL";
  type?: string;
  runtime_tier?: "L3_LOCAL" | "L2_GATEWAY" | "L1_CLOUD";
  required_mcps?: string[];
  required_models?: string[];
  recovery_playbook?: unknown;
}

function normalizePluginItemType(raw: string): "SKILL" | "MCP" | "TOOL" | "MODEL" {
  const u = String(raw).toUpperCase();
  if (u === "MCP") return "MCP";
  if (u === "TOOL") return "TOOL";
  if (u === "MODEL") return "MODEL";
  return "SKILL";
}

/**
 * 从 Authorization: Bearer <token> 解析并校验，返回 developer_id
 */
function resolveDeveloperFromToken(authHeader: string | null): string | null {
  if (!authHeader?.startsWith("Bearer ")) return null;
  const token = authHeader.slice(7).trim();
  const expected = readLocalEnvValue("JACHIN_DEV_TOKEN");
  const devId = readLocalEnvValue("JACHIN_DEV_ID");
  if (!expected || !devId || token !== expected) return null;
  return devId;
}

/**
 * 解析 multipart 中的 zip 并校验 plugin.json
 */
function parseAndValidateZip(zipBuffer: Buffer): {
  pluginJson: PluginJson;
  raw: unknown;
  contractWarnings: string[];
  qualityScore: number;
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
    const content = pluginEntry.getData().toString("utf8").replace(/^\uFEFF/, "");
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
  const itemTypeNorm = normalizePluginItemType(itemType);

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
  const requiredModels = Array.isArray(p.required_models)
    ? (p.required_models as string[]).filter((x) => typeof x === "string")
    : [];

  const pluginJson: PluginJson = {
    id,
    name,
    version,
    description: typeof p.description === "string" ? p.description : undefined,
    item_type: itemTypeNorm,
    runtime_tier: runtimeTierNorm,
    required_mcps: requiredMcps,
    required_models: requiredModels,
    recovery_playbook: p.recovery_playbook,
  };

  validateRecoveryPlaybook(p);
  const contract = validateCapabilityContract(p);

  // 076: 若包内含 config/ 则必须含 manifest.yaml
  validateConfigInZip(zip);

  return { pluginJson, raw, contractWarnings: contract.warnings, qualityScore: contract.qualityScore };
}

function validateCapabilityContract(manifest: Record<string, unknown>): { warnings: string[]; qualityScore: number } {
  validateDecomposition(manifest);
  validateDependencyList(manifest, "required_mcps");
  validateDependencyList(manifest, "required_models");
  const qualityScore = capabilityQualityScore(manifest);
  const warnings =
    qualityScore < 0.72
      ? [`capability_profile_low_quality: score ${qualityScore.toFixed(2)} < 0.72; can publish, but not production-grade`]
      : [];
  return { warnings, qualityScore };
}

function validateDecomposition(manifest: Record<string, unknown>): void {
  const decomposition = manifestValueOrMetadata(manifest, "decomposition");
  if (decomposition === undefined) return;
  const errors: string[] = [];
  if (!isRecord(decomposition)) {
    throwInvalidCapabilityContract(["decomposition must be an object"]);
  }
  const nodes = decomposition.nodes;
  if (!Array.isArray(nodes) || nodes.length === 0) {
    throwInvalidCapabilityContract(["decomposition.nodes must be a non-empty array"]);
  }
  const nodeIds = new Set<string>();
  nodes.forEach((node, index) => {
    const prefix = `decomposition.nodes[${index}]`;
    if (!isRecord(node)) {
      errors.push(`${prefix} must be an object`);
      return;
    }
    const nodeId = nonEmptyString(node.id) ? node.id.trim() : nonEmptyString(node.name) ? node.name.trim() : String(index);
    nodeIds.add(nodeId);
    for (const key of ["goal", "role_agent", "tool"] as const) {
      if (!nonEmptyString(node[key])) {
        errors.push(`${prefix}.${key} must be a non-empty string`);
      }
    }
    const verification = node.verification_criteria ?? node.verification ?? node.expected_evidence;
    if (!Array.isArray(verification) || verification.length === 0) {
      errors.push(`${prefix}.verification_criteria must be a non-empty array`);
    }
  });
  nodes.forEach((node, index) => {
    if (!isRecord(node)) return;
    for (const dep of stringItems(node.depends_on)) {
      if (!nodeIds.has(dep)) {
        errors.push(`decomposition.nodes[${index}].depends_on references unknown node: ${dep}`);
      }
    }
  });
  if (errors.length > 0) {
    throwInvalidCapabilityContract(errors);
  }
}

function validateDependencyList(manifest: Record<string, unknown>, key: string): void {
  const value = manifestValueOrMetadata(manifest, key);
  if (value === undefined) return;
  if (!Array.isArray(value)) {
    throwInvalidCapabilityContract([`${key} must be a string/object array`]);
  }
  const errors: string[] = [];
  value.forEach((item, index) => {
    const ok =
      nonEmptyString(item) ||
      (isRecord(item) && (nonEmptyString(item.id) || nonEmptyString(item.plugin_id) || nonEmptyString(item.model_id)));
    if (!ok) errors.push(`${key}[${index}] must be a non-empty string or dependency object`);
  });
  if (errors.length > 0) throwInvalidCapabilityContract(errors);
}

function capabilityQualityScore(manifest: Record<string, unknown>): number {
  const missing = new Set<string>();
  const inputs = manifestValueOrMetadata(manifest, "inputs");
  if (!Array.isArray(inputs) || inputs.length === 0) missing.add("inputs");
  const verification =
    manifestValueOrMetadata(manifest, "verification") ??
    manifestValueOrMetadata(manifest, "verification_methods");
  if (!Array.isArray(verification) || verification.length === 0) missing.add("verification");
  const examples = manifestValueOrMetadata(manifest, "examples");
  if (!Array.isArray(examples) || examples.length === 0) missing.add("examples");
  const risk = String(manifestValueOrMetadata(manifest, "risk") ?? "").trim().toLowerCase();
  if (!risk) missing.add("risk");
  if (["external_effect", "high", "critical"].includes(risk) && manifest.recovery_playbook === undefined) {
    missing.add("recovery_playbook");
  }
  const penalty = Math.min(0.85, missing.size * 0.16);
  return Math.round((1 - penalty) * 1000) / 1000;
}

function manifestValueOrMetadata(manifest: Record<string, unknown>, key: string): unknown {
  if (manifest[key] !== undefined) return manifest[key];
  const metadata = manifest.metadata;
  return isRecord(metadata) ? metadata[key] : undefined;
}

function stringItems(value: unknown): string[] {
  if (nonEmptyString(value)) return [value.trim()];
  if (!Array.isArray(value)) return [];
  return value.filter(nonEmptyString).map((item) => item.trim());
}

function throwInvalidCapabilityContract(errors: string[]): never {
  throw new PublishError(
    400,
    "INVALID_CAPABILITY_CONTRACT",
    `capability contract 格式错误：${errors.slice(0, 8).join("；")}`
  );
}

function validateRecoveryPlaybook(manifest: Record<string, unknown>): void {
  const playbook = manifest.recovery_playbook;
  if (playbook === undefined || playbook === null) return;
  const errors: string[] = [];
  if (!isRecord(playbook)) {
    throwInvalidRecoveryPlaybook(["recovery_playbook must be an object"]);
  }
  const targets = playbook.targets;
  if (!Array.isArray(targets) || targets.length === 0) {
    throwInvalidRecoveryPlaybook(["recovery_playbook.targets must be a non-empty array"]);
  }

  targets.forEach((target, targetIndex) => {
    const prefix = `recovery_playbook.targets[${targetIndex}]`;
    if (!isRecord(target)) {
      errors.push(`${prefix} must be an object`);
      return;
    }
    const roleAgent = target.role_agent ?? target.role;
    if (!nonEmptyString(roleAgent)) {
      errors.push(`${prefix}.role_agent must be a non-empty string`);
    }
    const tools = target.tools ?? target.tool_patterns;
    if (tools !== undefined) {
      validateStringArray(tools, `${prefix}.tools`, true, errors);
    }
    if (target.max_attempts !== undefined && !intInRange(target.max_attempts, 1, 8)) {
      errors.push(`${prefix}.max_attempts must be an integer from 1 to 8`);
    }
    const steps = target.steps;
    if (!Array.isArray(steps) || steps.length === 0) {
      errors.push(`${prefix}.steps must be a non-empty array`);
      return;
    }
    steps.forEach((step, stepIndex) => validateRecoveryStep(step, `${prefix}.steps[${stepIndex}]`, errors));
  });

  if (errors.length > 0) {
    throwInvalidRecoveryPlaybook(errors);
  }
}

function validateRecoveryStep(value: unknown, prefix: string, errors: string[]): void {
  if (!isRecord(value)) {
    errors.push(`${prefix} must be an object`);
    return;
  }
  if (!nonEmptyString(value.strategy)) {
    errors.push(`${prefix}.strategy must be a non-empty string`);
  }
  if (!nonEmptyString(value.tool)) {
    errors.push(`${prefix}.tool must be a non-empty string, use '$same' to retry the same tool`);
  }
  if (value.priority !== undefined && !intInRange(value.priority, 0, 1000)) {
    errors.push(`${prefix}.priority must be an integer from 0 to 1000`);
  }
  if (value.rationale !== undefined && typeof value.rationale !== "string") {
    errors.push(`${prefix}.rationale must be a string when provided`);
  }
  if (value.action_patch !== undefined && value.action_template !== undefined) {
    errors.push(`${prefix} cannot define both action_patch and action_template`);
  }
  for (const key of ["action_patch", "action_template"] as const) {
    if (value[key] !== undefined && !isRecord(value[key])) {
      errors.push(`${prefix}.${key} must be an object when provided`);
    }
  }
  if (value.when !== undefined) {
    validateRecoveryWhen(value.when, `${prefix}.when`, errors);
  }
}

function validateRecoveryWhen(value: unknown, prefix: string, errors: string[]): void {
  if (!isRecord(value)) {
    errors.push(`${prefix} must be an object when provided`);
    return;
  }
  for (const key of ["failure_any", "failure_all", "tool_not_contains"] as const) {
    if (value[key] !== undefined) {
      validateStringArray(value[key], `${prefix}.${key}`, false, errors);
    }
  }
  if (value.after_attempt !== undefined && !intInRange(value.after_attempt, 1, 8)) {
    errors.push(`${prefix}.after_attempt must be an integer from 1 to 8`);
  }
}

function validateStringArray(value: unknown, pathName: string, requireNonEmpty: boolean, errors: string[]): void {
  if (!Array.isArray(value)) {
    errors.push(`${pathName} must be a string array`);
    return;
  }
  if (requireNonEmpty && value.length === 0) {
    errors.push(`${pathName} must be a non-empty string array`);
    return;
  }
  value.forEach((item, index) => {
    if (!nonEmptyString(item)) {
      errors.push(`${pathName}[${index}] must be a non-empty string`);
    }
  });
}

function throwInvalidRecoveryPlaybook(errors: string[]): never {
  throw new PublishError(
    400,
    "INVALID_RECOVERY_PLAYBOOK",
    `recovery_playbook 格式错误：${errors.slice(0, 8).join("；")}`
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function nonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function intInRange(value: unknown, min: number, max: number): boolean {
  return Number.isInteger(value) && typeof value === "number" && value >= min && value <= max;
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
 * Skill dependencies must already exist in L1.
 *
 * Current stage has no external developer marketplace review. Published
 * packages are approved immediately, so this only checks dependency presence.
 */
async function validateRequiredMcpsForSkill(
  db: ReturnType<typeof getDb>,
  requiredMcps: string[] | undefined
): Promise<void> {
  if (!db) return;
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
  for (const m of mcps) {
    const pid = (m.pluginId ?? "").toLowerCase();
    if (pluginIdsLower.has(pid)) {
      foundLower.add(pid);
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
  // 禁止写成 a || b ? c : d：当 NEXUS_PUBLIC_URL 已设而 VERCEL_URL 未设时，旧写法会走到
  // `https://${VERCEL_URL}` → https://undefined（L2 无法拉包）。
  const pub = (readLocalEnvValue("NEXUS_PUBLIC_URL") || "").trim().replace(/\/$/, "");
  const vercel = (process.env.VERCEL_URL || "").trim();
  let baseUrl: string;
  if (pub) {
    baseUrl = pub;
  } else if (vercel) {
    const host = vercel.replace(/^https?:\/\//, "").replace(/\/$/, "");
    baseUrl = `https://${host}`;
  } else {
    baseUrl = "http://localhost:3000";
  }
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
  const itemTypeNorm = normalizePluginItemType(itemTypeRaw);

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
    required_models: [],
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
    appendL1DebugLine("store.publish", {
      msg: "request",
      has_auth: Boolean(authHeader?.startsWith("Bearer ")),
      developer_ok: Boolean(developerId),
      publish_policy: "direct_approved",
    });
    if (!developerId) {
      appendL1DebugLine("store.publish", { msg: "reject", reason: "INVALID_TOKEN" });
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
    let contractWarnings: string[] = [];
    let contractQualityScore = 0;
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
        contractWarnings = parsed.contractWarnings;
        contractQualityScore = parsed.qualityScore;
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
      appendL1DebugLine("store.publish", {
        msg: "no_database",
        plugin_id: pluginJson.id,
        item_type: pluginJson.item_type,
      });
      return NextResponse.json({
        success: true,
        message: shadowOnly ? "影子上传接收成功（数据库未配置）" : "发布接收成功（数据库未配置，未入库）",
        plugin_id: pluginJson.id,
        name: pluginJson.name,
        version: pluginJson.version,
        package_url: packageUrl ?? undefined,
        contract_warnings: contractWarnings,
        capability_quality_score: contractQualityScore,
      });
    }

    const db = getDb()!;

    // Skill dependencies still need to exist, but no review gate is required.
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
      itemType: (pluginJson.item_type ?? "SKILL") as "SKILL" | "MCP" | "TOOL" | "MODEL",
      name: pluginJson.name,
      description: pluginJson.description ?? null,
      developerId,
      visibility: visibilityNorm,
      priceMonthly,
      runtimeTier: (pluginJson.runtime_tier ?? "L3_LOCAL") as
        | "L3_LOCAL"
        | "L2_GATEWAY"
        | "L1_CLOUD",
      requiredMcps: [
        ...(pluginJson.required_mcps ?? []),
        ...(pluginJson.required_models ?? []).map((id) => `model:${id}`),
      ],
      packageUrl: packageUrl ?? null,
      packageSha256: packageSha256 ?? null,
      manifestJson: manifestJsonRaw,
      category: pluginJson.item_type === "MODEL" ? "model" : pluginJson.item_type === "MCP" ? "mcp" : pluginJson.item_type === "TOOL" ? "tool" : "skill",
      status: "approved",
      updatedAt: new Date(),
    };

    let itemUuid: string | undefined;
    if (existing.length > 0) {
      // 更新时不要 SET plugin_id：部分 PG/Drizzle 组合下对唯一键列自赋值会报错；WHERE 已限定行。
      const { pluginId: _omitPluginId, ...updateRow } = row;
      void _omitPluginId;
      await db
        .update(pluginsRegistry)
        .set(updateRow)
        .where(eq(pluginsRegistry.pluginId, pluginJson.id));
      itemUuid = existing[0].id;
    } else {
      const ins = await db.insert(pluginsRegistry).values(row).returning({ id: pluginsRegistry.id });
      itemUuid = ins[0]?.id;
    }

    appendL1DebugLine("store.publish", {
      msg: "upsert_ok",
      plugin_id: pluginJson.id,
      item_id: itemUuid,
      item_type: row.itemType,
      status: row.status,
      visibility: row.visibility,
      shadow_only: shadowOnly,
    });

    return NextResponse.json({
      success: true,
      message: shadowOnly ? "影子上传成功，私有包已登记，实体请侧载到 L2" : "发布成功",
      plugin_id: pluginJson.id,
      item_id: itemUuid,
      status: row.status,
      name: pluginJson.name,
      version: pluginJson.version,
      package_url: packageUrl ?? undefined,
      contract_warnings: contractWarnings,
      capability_quality_score: contractQualityScore,
    });
  } catch (err) {
    console.error("[store/publish] Error:", err);
    appendL1DebugError("store.publish", err);
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
