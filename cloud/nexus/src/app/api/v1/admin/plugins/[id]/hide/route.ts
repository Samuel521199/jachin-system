/**
 * POST /api/v1/admin/plugins/[id]/hide
 * 隐藏已上架插件：visibility = PRIVATE，商城不展示，manifest 不再下发
 * 仅 isRoot 可访问
 */
import { NextRequest, NextResponse } from "next/server";
import { getDb, isDatabaseConfigured } from "@/db";
import { pluginsRegistry } from "@/db/schema";
import { eq } from "drizzle-orm";
import { requireIsRoot } from "@/lib/admin-auth";

export const dynamic = "force-dynamic";

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const forbidden = requireIsRoot(request);
  if (forbidden) return forbidden;

  const { id } = await params;
  if (!id?.trim()) {
    return NextResponse.json(
      { success: false, error: "Missing plugin id" },
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
  const isUuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id.trim());
  const whereClause = isUuid
    ? eq(pluginsRegistry.id, id.trim())
    : eq(pluginsRegistry.pluginId, id.trim());

  const [existing] = await db
    .select({ id: pluginsRegistry.id, visibility: pluginsRegistry.visibility })
    .from(pluginsRegistry)
    .where(whereClause)
    .limit(1);

  if (!existing) {
    return NextResponse.json(
      { success: false, error: "Plugin not found" },
      { status: 404 }
    );
  }

  if (existing.visibility === "PRIVATE") {
    return NextResponse.json({
      success: true,
      message: "插件已处于隐藏状态",
      id: existing.id,
    });
  }

  await db
    .update(pluginsRegistry)
    .set({ visibility: "PRIVATE", updatedAt: new Date() })
    .where(eq(pluginsRegistry.id, existing.id!));

  return NextResponse.json({
    success: true,
    message: "插件已隐藏，商城不展示，manifest 不再下发",
    id: existing.id,
  });
}
