/**
 * GET /api/v1/developer/plugins
 * 开发者作品列表 — 按 developer_id 列出其发布的 Skill/MCP
 *
 * 鉴权：X-Developer-Id 或 query developer_id（演示模式允许 query 传入）
 */
import { NextRequest, NextResponse } from "next/server";
import { getDb, isDatabaseConfigured } from "@/db";
import { pluginsRegistry } from "@/db/schema";
import { eq, desc } from "drizzle-orm";
import { extractDeveloperId } from "@/lib/tenant";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  try {
    let developerId = extractDeveloperId(request);
    if (!developerId) {
      const { searchParams } = new URL(request.url);
      developerId = searchParams.get("developer_id")?.trim() ?? null;
    }
    if (!developerId) {
      return NextResponse.json(
        {
          success: false,
          error: "UNAUTHORIZED",
          message: "Provide X-Developer-Id header or ?developer_id=xxx",
        },
        { status: 401 }
      );
    }

    if (!isDatabaseConfigured()) {
      return NextResponse.json({
        success: true,
        data: [],
        meta: { total: 0 },
      });
    }

    const db = getDb()!;

    const rows = await db
      .select({
        id: pluginsRegistry.id,
        pluginId: pluginsRegistry.pluginId,
        version: pluginsRegistry.version,
        itemType: pluginsRegistry.itemType,
        name: pluginsRegistry.name,
        description: pluginsRegistry.description,
        developerId: pluginsRegistry.developerId,
        visibility: pluginsRegistry.visibility,
        priceMonthly: pluginsRegistry.priceMonthly,
        runtimeTier: pluginsRegistry.runtimeTier,
        packageUrl: pluginsRegistry.packageUrl,
        packageSha256: pluginsRegistry.packageSha256,
        status: pluginsRegistry.status,
        createdAt: pluginsRegistry.createdAt,
      })
      .from(pluginsRegistry)
      .where(eq(pluginsRegistry.developerId, developerId))
      .orderBy(desc(pluginsRegistry.createdAt));

    const data = rows.map((r) => ({
      id: r.id,
      plugin_id: r.pluginId,
      version: r.version,
      item_type: r.itemType,
      name: r.name,
      description: r.description ?? null,
      developer_id: r.developerId ?? null,
      visibility: r.visibility,
      price_monthly: r.priceMonthly ? Number(r.priceMonthly) : 0,
      runtime_tier: r.runtimeTier,
      package_url: r.packageUrl ?? null,
      package_sha256: r.packageSha256 ?? null,
      status: r.status,
      created_at: r.createdAt?.toISOString() ?? null,
    }));

    return NextResponse.json({
      success: true,
      data,
      meta: { total: data.length },
    });
  } catch (e) {
    console.error("[developer/plugins] Error:", e);
    return NextResponse.json(
      { success: false, error: "Internal server error" },
      { status: 500 }
    );
  }
}
