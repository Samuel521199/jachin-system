import { NextRequest, NextResponse } from "next/server";
import { getDb, isDatabaseConfigured } from "@/db";
import { pluginsRegistry } from "@/db/schema";
import { eq, desc } from "drizzle-orm";
import { requireIsRoot } from "@/lib/admin-auth";

export const dynamic = "force-dynamic";

/**
 * GET /api/v1/admin/review
 * 列出所有 status = 'pending' 的待审插件，按提交时间倒序。
 * 仅 isRoot 可访问。
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
    console.error("[admin/review] GET Error:", e);
    return NextResponse.json(
      { success: false, error: "Internal server error" },
      { status: 500 }
    );
  }
}

/**
 * POST /api/v1/admin/review
 * Body: { plugin_id: "uuid 或 pluginId", action: "APPROVE" | "REJECT", reason?: "可选理由" }
 * 仅 isRoot 可访问。
 */
export async function POST(request: NextRequest) {
  const forbidden = requireIsRoot(request);
  if (forbidden) return forbidden;

  try {
    if (!isDatabaseConfigured()) {
      return NextResponse.json(
        { success: false, error: "Database not configured" },
        { status: 503 }
      );
    }

    let body: { plugin_id?: string; action?: string; reason?: string };
    try {
      body = (await request.json()) as { plugin_id?: string; action?: string; reason?: string };
    } catch {
      return NextResponse.json(
        { success: false, error: "Invalid JSON body" },
        { status: 400 }
      );
    }

    const pluginIdRaw = body.plugin_id?.trim();
    const action = (body.action ?? "").toUpperCase();
    const reason = (body.reason ?? "").trim().slice(0, 500);

    if (!pluginIdRaw) {
      return NextResponse.json(
        { success: false, error: "Missing plugin_id" },
        { status: 400 }
      );
    }

    if (action !== "APPROVE" && action !== "REJECT") {
      return NextResponse.json(
        { success: false, error: "action must be APPROVE or REJECT" },
        { status: 400 }
      );
    }

    const db = getDb()!;

    const isUuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(pluginIdRaw);
    const whereClause = isUuid ? eq(pluginsRegistry.id, pluginIdRaw) : eq(pluginsRegistry.pluginId, pluginIdRaw);

    const [existing] = await db
      .select({ id: pluginsRegistry.id, status: pluginsRegistry.status })
      .from(pluginsRegistry)
      .where(whereClause)
      .limit(1);

    if (!existing) {
      return NextResponse.json(
        { success: false, error: "Plugin not found" },
        { status: 404 }
      );
    }

    if (existing.status !== "pending") {
      return NextResponse.json(
        { success: false, error: `Plugin is not pending (current: ${existing.status})` },
        { status: 400 }
      );
    }

    if (action === "APPROVE") {
      await db
        .update(pluginsRegistry)
        .set({
          status: "approved",
          rejectReason: null,
          updatedAt: new Date(),
        })
        .where(eq(pluginsRegistry.id, existing.id!));

      return NextResponse.json({
        success: true,
        message: "插件已批准上架，面向全球 L2 开放同步",
        id: existing.id,
      });
    }

    await db
      .update(pluginsRegistry)
      .set({
        status: "rejected",
        rejectReason: reason || null,
        updatedAt: new Date(),
      })
      .where(eq(pluginsRegistry.id, existing.id!));

    return NextResponse.json({
      success: true,
      message: "插件已驳回",
      id: existing.id,
      reason: reason || null,
    });
  } catch (e) {
    console.error("[admin/review] POST Error:", e);
    return NextResponse.json(
      { success: false, error: "Internal server error" },
      { status: 500 }
    );
  }
}
