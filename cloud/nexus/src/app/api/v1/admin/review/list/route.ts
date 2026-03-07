import { NextRequest, NextResponse } from "next/server";
import { getDb, isDatabaseConfigured } from "@/db";
import { pluginsRegistry } from "@/db/schema";
import { eq, desc } from "drizzle-orm";
import { requireIsRoot } from "@/lib/admin-auth";

export const dynamic = "force-dynamic";

/**
 * GET /api/v1/admin/review/list
 * 获取所有 status = 'pending' 的待审插件列表（仅 isRoot）
 */
export async function GET(request: NextRequest) {
  const forbidden = requireIsRoot(request);
  if (forbidden) return forbidden;
  try {
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
        manifestJson: pluginsRegistry.manifestJson,
        status: pluginsRegistry.status,
        rejectReason: pluginsRegistry.rejectReason,
        createdAt: pluginsRegistry.createdAt,
      })
      .from(pluginsRegistry)
      .where(eq(pluginsRegistry.status, "pending"))
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
      manifest_json: r.manifestJson ?? null,
      status: r.status,
      reject_reason: r.rejectReason ?? null,
      created_at: r.createdAt?.toISOString() ?? null,
    }));

    return NextResponse.json({
      success: true,
      data,
      meta: { total: data.length },
    });
  } catch (e) {
    console.error("[admin/review/list] Error:", e);
    return NextResponse.json(
      { success: false, error: "Internal server error" },
      { status: 500 }
    );
  }
}
