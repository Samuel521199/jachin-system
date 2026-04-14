/**
 * POST /api/v1/store/unpublish
 * 开发者自助下架 — 将本人发布的插件归档（status = 'archived'）
 *
 * Body: { plugin_id: "uuid 或 pluginId" }
 * 鉴权：X-Developer-Id 或 Cookie nexus_developer_id，且必须与插件的 developer_id 一致
 */
import { NextRequest, NextResponse } from "next/server";
import { getDb, isDatabaseConfigured, describeDatabaseConnectError } from "@/db";
import { pluginsRegistry } from "@/db/schema";
import { eq } from "drizzle-orm";
import { extractDeveloperId } from "@/lib/tenant";

export const dynamic = "force-dynamic";
/** postgres-js / Drizzle 需 Node，避免偶发 Edge 或打包问题 */
export const runtime = "nodejs";

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

    try {
      await db
        .update(pluginsRegistry)
        .set({
          status: "archived",
          visibility: "PRIVATE",
          updatedAt: new Date(),
        })
        .where(eq(pluginsRegistry.id, existing.id!));
    } catch (updErr) {
      /** 部分旧库若对 status 有约束，先仅隐藏（商城只展示 PUBLIC+approved） */
      const hint = describeDatabaseConnectError(updErr);
      if (hint) {
        return NextResponse.json(
          { success: false, error: "DATABASE_UNAVAILABLE", message: hint },
          { status: 503 }
        );
      }
      try {
        await db
          .update(pluginsRegistry)
          .set({ visibility: "PRIVATE", updatedAt: new Date() })
          .where(eq(pluginsRegistry.id, existing.id!));
      } catch (e2) {
        const msg = updErr instanceof Error ? updErr.message : String(updErr);
        const msg2 = e2 instanceof Error ? e2.message : String(e2);
        console.error("[store/unpublish] update failed:", updErr, e2);
        return NextResponse.json(
          {
            success: false,
            error: "UNPUBLISH_FAILED",
            message: msg,
            fallback_error: msg2,
          },
          { status: 500 }
        );
      }
    }

    return NextResponse.json({
      success: true,
      message: "插件已下架（归档并设为私有），商城与 manifest 均不再展示",
      id: existing.id,
    });
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    const hint = describeDatabaseConnectError(e);
    console.error("[store/unpublish] Error:", e);
    if (hint) {
      return NextResponse.json(
        { success: false, error: "DATABASE_UNAVAILABLE", message: hint },
        { status: 503 }
      );
    }
    return NextResponse.json(
      { success: false, error: "Internal server error", message: msg },
      { status: 500 }
    );
  }
}
