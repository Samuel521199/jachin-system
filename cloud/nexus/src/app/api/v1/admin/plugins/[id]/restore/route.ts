/**
 * POST /api/v1/admin/plugins/[id]/restore
 * 从归档恢复：status = 'approved'，恢复上架
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

  if (existing.status === "approved") {
    return NextResponse.json({
      success: true,
      message: "插件已处于上架状态",
      id: existing.id,
    });
  }

  await db
    .update(pluginsRegistry)
    .set({ status: "approved", rejectReason: null, updatedAt: new Date() })
    .where(eq(pluginsRegistry.id, existing.id!));

  return NextResponse.json({
    success: true,
    message: "插件已恢复上架",
    id: existing.id,
  });
}
