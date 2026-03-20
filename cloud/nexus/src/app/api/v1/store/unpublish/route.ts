/**
 * POST /api/v1/store/unpublish
 * 开发者自助下架 — 将本人发布的插件归档（status = 'archived'）
 *
 * Body: { plugin_id: "uuid 或 pluginId" }
 * 鉴权：X-Developer-Id 或 Cookie nexus_developer_id，且必须与插件的 developer_id 一致
 */
import { NextRequest, NextResponse } from "next/server";
import { getDb, isDatabaseConfigured } from "@/db";
import { pluginsRegistry } from "@/db/schema";
import { eq } from "drizzle-orm";
import { extractDeveloperId } from "@/lib/tenant";

export const dynamic = "force-dynamic";

export async function POST(request: NextRequest) {
  try {
    const developerId = extractDeveloperId(request);
    if (!developerId) {
      return NextResponse.json(
        {
          success: false,
          error: "UNAUTHORIZED",
          message: "Provide X-Developer-Id header or set nexus_developer_id cookie",
        },
        { status: 401 }
      );
    }

    let body: { plugin_id?: string };
    try {
      body = (await request.json()) as { plugin_id?: string };
    } catch {
      return NextResponse.json(
        { success: false, error: "Invalid JSON body" },
        { status: 400 }
      );
    }

    const pluginIdRaw = body.plugin_id?.trim();
    if (!pluginIdRaw) {
      return NextResponse.json(
        { success: false, error: "Missing plugin_id" },
        { status: 400 }
      );
    }

    if (!isDatabaseConfigured()) {
      return NextResponse.json(
        { success: false, error: "Database not configured" },
        { status: 503 }
      );
    }

    const db = getDb()!;
    const isUuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(pluginIdRaw);
    const whereClause = isUuid
      ? eq(pluginsRegistry.id, pluginIdRaw)
      : eq(pluginsRegistry.pluginId, pluginIdRaw);

    const [existing] = await db
      .select({ id: pluginsRegistry.id, developerId: pluginsRegistry.developerId, status: pluginsRegistry.status })
      .from(pluginsRegistry)
      .where(whereClause)
      .limit(1);

    if (!existing) {
      return NextResponse.json(
        { success: false, error: "Plugin not found" },
        { status: 404 }
      );
    }

    if ((existing.developerId ?? "").toLowerCase() !== developerId.toLowerCase()) {
      return NextResponse.json(
        {
          success: false,
          error: "FORBIDDEN",
          message: "仅可下架本人发布的插件",
        },
        { status: 403 }
      );
    }

    if (existing.status === "archived") {
      return NextResponse.json({
        success: true,
        message: "插件已处于归档状态",
        id: existing.id,
      });
    }

    await db
      .update(pluginsRegistry)
      .set({ status: "archived", updatedAt: new Date() })
      .where(eq(pluginsRegistry.id, existing.id!));

    return NextResponse.json({
      success: true,
      message: "插件已下架归档，商城与 manifest 均不再展示",
      id: existing.id,
    });
  } catch (e) {
    console.error("[store/unpublish] Error:", e);
    return NextResponse.json(
      { success: false, error: "Internal server error" },
      { status: 500 }
    );
  }
}
