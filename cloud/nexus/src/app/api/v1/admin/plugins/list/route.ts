/**
 * GET /api/v1/admin/plugins/list?status=approved|archived|pending
 * 按状态列出插件，供管理员管理已上架/已归档（仅 isRoot）
 */
import { NextRequest, NextResponse } from "next/server";
import { getDb, isDatabaseConfigured } from "@/db";
import { pluginsRegistry } from "@/db/schema";
import { eq, desc } from "drizzle-orm";
import { requireIsRoot } from "@/lib/admin-auth";

export const dynamic = "force-dynamic";

const VALID_STATUSES = ["approved", "archived", "pending"] as const;

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

    const { searchParams } = new URL(request.url);
    const status = searchParams.get("status")?.toLowerCase() ?? "approved";
    if (!VALID_STATUSES.includes(status as (typeof VALID_STATUSES)[number])) {
      return NextResponse.json(
        { success: false, error: "status must be approved, archived, or pending" },
        { status: 400 }
      );
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
      .where(eq(pluginsRegistry.status, status))
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
    console.error("[admin/plugins/list] Error:", e);
    return NextResponse.json(
      { success: false, error: "Internal server error" },
      { status: 500 }
    );
  }
}
