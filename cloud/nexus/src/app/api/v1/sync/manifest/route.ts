import { NextRequest, NextResponse } from "next/server";
import { getDb, isDatabaseConfigured } from "@/db";
import { pluginsRegistry, userLicenses } from "@/db/schema";
import { eq, and, or, gt, inArray, isNull } from "drizzle-orm";
import {
  assertOrganizationExists,
  assertUserMemberOfOrganization,
  extractJwtSubjectFromRequest,
  extractTenantIdAllowingMachineFallback,
  isTenantUuidString,
} from "@/lib/tenant";
import {
  extractBearerTokenRaw,
  resolveEdgeAgentManifestContext,
  resolveTenantIdFromEdgeAgentBearer,
} from "@/lib/edge-agent-manifest-auth";
import { rateLimit, MANIFEST_LIMIT } from "@/lib/ratelimit";

export const dynamic = "force-dynamic";

export interface ManifestItem {
  id: string;
  item_type: string;
  name: string;
  description: string | null;
  runtime_tier: string;
  package_url: string | null;
  package_sha256: string | null;
  required_mcps: string[];
  version: string;
  changelog?: string | null;
}

/**
 * GET /api/v1/sync/manifest
 * 面向 L2 边缘网关的「神谕同步」
 *
 * 状态锁死：仅同步 status = 'approved' 的已核准公共物资，未审核的绝不下发。
 *
 * 请求头：Authorization Bearer = L2 配对 access_token（edge_agents）时，默认解析为单租户；
 * **P3**：可再带 `X-Tenant-Id: <organizations.id>`，须为该 edge 绑定 **用户** 的成员工作区。
 * 非 edge 时需提供 tenant_id（X-Tenant-Id 或 JWT Bearer 中的 tenant_id/org_id）。
 *
 * 逻辑：
 * - 查 user_licenses 表，找出该 tenant_id 下 status = 'ACTIVE' 且未过期的 item_id
 * - JOIN plugins_registry 获取详情，仅拉取 status = 'approved' 的插件
 * - Skill 依赖解析：遍历 SKILL 的 required_mcps，将缺失的 MCP 自动加入 manifest（即使用户未单独订阅）
 * - 返回 Manifest：id、类型、runtime_tier、package_url、required_mcps
 *
 * L2 网关据此下载 Wasm 包、拉起 MCP 驱动。
 */
export async function GET(request: NextRequest) {
  try {
    const { ok } = rateLimit(request, MANIFEST_LIMIT);
    if (!ok) {
      return NextResponse.json(
        { success: false, error: "RATE_LIMIT_EXCEEDED", message: "请求过于频繁，请稍后再试" },
        { status: 429 }
      );
    }

    const bearer = extractBearerTokenRaw(request);

    let tenantId: string | null = null;
    let tenantSource: "edge_agent" | "header_session" = "header_session";

    if (isDatabaseConfigured() && bearer) {
      const dbEarly = getDb()!;
      const edgeCtx = await resolveEdgeAgentManifestContext(dbEarly, bearer);
      if (edgeCtx) {
        tenantSource = "edge_agent";
        const hdr =
          request.headers.get("X-Tenant-Id")?.trim() ??
          request.headers.get("x-tenant-id")?.trim() ??
          "";
        if (hdr && isTenantUuidString(hdr)) {
          if (edgeCtx.userId) {
            const member = await assertUserMemberOfOrganization(
              dbEarly,
              hdr,
              edgeCtx.userId
            );
            if (!member) {
              return NextResponse.json(
                {
                  success: false,
                  error: "Forbidden",
                  message: "Edge 凭证对应用户非该 X-Tenant-Id 工作区成员",
                },
                { status: 403 }
              );
            }
            tenantId = hdr;
          } else if (
            edgeCtx.organizationId &&
            hdr === edgeCtx.organizationId.trim()
          ) {
            tenantId = hdr;
          } else {
            return NextResponse.json(
              {
                success: false,
                error: "Forbidden",
                message:
                  "未绑定用户的边缘凭证仅允许拉取已配对组织；请使 X-Tenant-Id 与 edge_agents.organization_id 一致",
              },
              { status: 403 }
            );
          }
        } else {
          tenantId = await resolveTenantIdFromEdgeAgentBearer(dbEarly, bearer);
        }
      }
    }

    if (!tenantId) {
      tenantId = await extractTenantIdAllowingMachineFallback(request);
    }

    if (!tenantId) {
      return NextResponse.json(
        {
          success: false,
          error: "Missing tenant_id",
          message:
            "Provide L2 pairing Bearer token, or X-Tenant-Id / Bearer org_id (organizations UUID)",
        },
        { status: 401 }
      );
    }

    if (!isTenantUuidString(tenantId.trim())) {
      return NextResponse.json(
        {
          success: false,
          error: "Invalid tenant_id",
          message: "tenant_id must be organizations.id (UUID)",
        },
        { status: 400 }
      );
    }

    if (!isDatabaseConfigured()) {
      return NextResponse.json(
        {
          success: true,
          manifest: [],
          meta: { tenant_id: tenantId, total: 0, source: "fallback" },
        },
        { status: 200 }
      );
    }

    const db = getDb()!;
    const tid = tenantId.trim();
    const orgOk = await assertOrganizationExists(db, tid);
    if (!orgOk) {
      return NextResponse.json(
        {
          success: false,
          error: "Unknown tenant",
          message: "tenant_id is not a registered organization",
        },
        { status: 404 }
      );
    }
    /** L2 配对 token 非 Auth.js JWT，不触发 sub 与组织成员校验；浏览器 JWT 仍校验 */
    if (tenantSource !== "edge_agent") {
      const jwtSub = extractJwtSubjectFromRequest(request);
      if (jwtSub) {
        const member = await assertUserMemberOfOrganization(db, tid, jwtSub);
        if (!member) {
          return NextResponse.json(
            {
              success: false,
              error: "Forbidden",
              message: "JWT sub is not a member of this organization",
            },
            { status: 403 }
          );
        }
      }
    }

    const now = new Date();

    // 1. 查 user_licenses：status = ACTIVE 且 (expires_at IS NULL 或 expires_at > now)
    const activeLicenses = await db
      .select({
        itemId: userLicenses.itemId,
      })
      .from(userLicenses)
      .where(
        and(
          eq(userLicenses.tenantId, tid),
          eq(userLicenses.status, "ACTIVE"),
          or(
            isNull(userLicenses.expiresAt),
            gt(userLicenses.expiresAt, now)
          )
        )
      );

    const itemIds = activeLicenses.map((l) => l.itemId).filter(Boolean);
    if (itemIds.length === 0) {
      return NextResponse.json({
        success: true,
        manifest: [],
        meta: { tenant_id: tid, total: 0 },
      });
    }

    // 2. JOIN plugins_registry 获取详情，仅拉取 status = 'approved' 且 visibility = 'PUBLIC' 的公共插件（排除已隐藏、已归档）
    const items = await db
      .select({
        id: pluginsRegistry.id,
        pluginId: pluginsRegistry.pluginId,
        itemType: pluginsRegistry.itemType,
        name: pluginsRegistry.name,
        description: pluginsRegistry.description,
        runtimeTier: pluginsRegistry.runtimeTier,
        packageUrl: pluginsRegistry.packageUrl,
        packageSha256: pluginsRegistry.packageSha256,
        requiredMcps: pluginsRegistry.requiredMcps,
        version: pluginsRegistry.version,
      })
      .from(pluginsRegistry)
      .where(
        and(
          inArray(pluginsRegistry.id, itemIds),
          eq(pluginsRegistry.status, "approved"),
          eq(pluginsRegistry.visibility, "PUBLIC")
        )
      );

    const manifest: ManifestItem[] = items.map((r) => ({
      id: r.id!,
      item_type: r.itemType,
      name: r.name,
      description: r.description ?? null,
      runtime_tier: r.runtimeTier,
      package_url: r.packageUrl ?? null,
      package_sha256: r.packageSha256 ?? null,
      required_mcps: (r.requiredMcps as string[]) ?? [],
      version: r.version ?? "1.0.0",
    }));

    // 3. Skill 依赖解析：收集 required_mcps，将缺失的 L3_LOCAL MCP 加入 manifest
    const existingPluginIds = new Set(
      items.map((r) => (r.pluginId ?? "").toLowerCase()).filter(Boolean)
    );
    const requiredMcpPluginIds = new Set<string>();
    for (const item of items) {
      if (item.itemType !== "SKILL") continue;
      const rmcps = (item.requiredMcps as string[]) ?? [];
      for (const rm of rmcps) {
        if (typeof rm !== "string" || !rm.trim()) continue;
        const pid = rm.replace(/^mcp:/i, "").trim().toLowerCase();
        if (pid && !existingPluginIds.has(pid)) {
          requiredMcpPluginIds.add(pid);
        }
      }
    }

    if (requiredMcpPluginIds.size > 0) {
      const depMcps = await db
        .select({
          id: pluginsRegistry.id,
          pluginId: pluginsRegistry.pluginId,
          itemType: pluginsRegistry.itemType,
          name: pluginsRegistry.name,
          description: pluginsRegistry.description,
          runtimeTier: pluginsRegistry.runtimeTier,
          packageUrl: pluginsRegistry.packageUrl,
          packageSha256: pluginsRegistry.packageSha256,
          requiredMcps: pluginsRegistry.requiredMcps,
          version: pluginsRegistry.version,
        })
        .from(pluginsRegistry)
        .where(
          and(
            eq(pluginsRegistry.itemType, "MCP"),
            eq(pluginsRegistry.status, "approved"),
            eq(pluginsRegistry.visibility, "PUBLIC")
          )
        );

      const manifestIds = new Set(manifest.map((m) => m.id));
      for (const mcp of depMcps) {
        const pid = mcp.pluginId;
        const pluginIdLower = (pid ?? "").toLowerCase();
        if (!requiredMcpPluginIds.has(pluginIdLower)) continue;
        if (!mcp.packageUrl) continue;
        if (manifestIds.has(mcp.id!)) continue;

        manifest.push({
          id: mcp.id!,
          item_type: mcp.itemType,
          name: mcp.name,
          description: mcp.description ?? null,
          runtime_tier: mcp.runtimeTier,
          package_url: mcp.packageUrl ?? null,
          package_sha256: mcp.packageSha256 ?? null,
          required_mcps: (mcp.requiredMcps as string[]) ?? [],
          version: mcp.version ?? "1.0.0",
        });
        manifestIds.add(mcp.id!);
      }
    }

    return NextResponse.json({
      success: true,
      manifest,
      meta: { tenant_id: tid, total: manifest.length },
    });
  } catch (e) {
    console.error("[sync/manifest] Unexpected error:", e);
    return NextResponse.json(
      { success: false, error: "Internal server error" },
      { status: 500 }
    );
  }
}
