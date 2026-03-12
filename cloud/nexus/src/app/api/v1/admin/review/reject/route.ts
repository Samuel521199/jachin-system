import { NextRequest, NextResponse } from "next/server";
import { getDb, isDatabaseConfigured } from "@/db";
import { pluginsRegistry } from "@/db/schema";
import { eq } from "drizzle-orm";
import { requireIsRoot } from "@/lib/admin-auth";

export const dynamic = "force-dynamic";

/**
 * POST /api/v1/admin/review/reject
 * 将插件状态改为 rejected（驳回），仅 isRoot
 * Body: { id: "uuid", reason?: "驳回理由" }
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

    let body: { id?: string; reason?: string };
    try {
      body = (await request.json()) as { id?: string; reason?: string };
    } catch {
      return NextResponse.json(
        { success: false, error: "Invalid JSON body" },
        { status: 400 }
      );
    }

    const id = body.id?.trim();
    if (!id) {
      return NextResponse.json(
        { success: false, error: "Missing id" },
        { status: 400 }
      );
    }

    const reason = (body.reason ?? "").trim().slice(0, 500);

    const db = getDb()!;

    const [existing] = await db
      .select({ id: pluginsRegistry.id, status: pluginsRegistry.status })
      .from(pluginsRegistry)
      .where(eq(pluginsRegistry.id, id))
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

    await db
      .update(pluginsRegistry)
      .set({
        status: "rejected",
        rejectReason: reason || null,
        updatedAt: new Date(),
      })
      .where(eq(pluginsRegistry.id, id));

    return NextResponse.json({
      success: true,
      message: "插件已驳回",
      id,
      reason: reason || null,
    });
  } catch (e) {
    console.error("[admin/review/reject] Error:", e);
    return NextResponse.json(
      { success: false, error: "Internal server error" },
      { status: 500 }
    );
  }
}
