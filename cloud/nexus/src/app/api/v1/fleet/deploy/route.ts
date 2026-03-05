import { NextRequest, NextResponse } from "next/server";
import { getDb, isDatabaseConfigured } from "@/db";
import { edgeAgents, blueprints } from "@/db/schema";
import { eq, inArray, and } from "drizzle-orm";

const DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000001";

/**
 * POST /api/v1/fleet/deploy
 * 批量下发蓝图 - 更新多台 edge_agents 的 current_blueprint_id
 */
export async function POST(req: NextRequest) {
  try {
    const body = await req.json().catch(() => ({}));
    const { agent_ids, blueprint_id, user_id } = body;

    if (!Array.isArray(agent_ids) || agent_ids.length === 0) {
      return NextResponse.json(
        { success: false, error: "agent_ids required (non-empty array)" },
        { status: 400 }
      );
    }
    if (!blueprint_id || typeof blueprint_id !== "string") {
      return NextResponse.json(
        { success: false, error: "blueprint_id required" },
        { status: 400 }
      );
    }

    if (!isDatabaseConfigured()) {
      return NextResponse.json({
        success: true,
        updated_count: agent_ids.length,
      });
    }

    const db = getDb()!;
    const userId = user_id ?? DEFAULT_USER_ID;

    const [bp] = await db
      .select({ id: blueprints.id })
      .from(blueprints)
      .where(eq(blueprints.id, blueprint_id))
      .limit(1);

    if (!bp) {
      return NextResponse.json(
        { success: false, error: "Blueprint not found" },
        { status: 404 }
      );
    }

    const result = await db
      .update(edgeAgents)
      .set({
        currentBlueprintId: blueprint_id,
        updatedAt: new Date(),
      })
      .where(
        and(
          inArray(edgeAgents.id, agent_ids),
          eq(edgeAgents.userId, userId)
        )
      )
      .returning({ id: edgeAgents.id });

    return NextResponse.json({
      success: true,
      updated_count: result.length,
    });
  } catch (e) {
    console.error("[fleet/deploy] Error:", e);
    return NextResponse.json(
      { success: false, error: "Bulk deploy failed" },
      { status: 500 }
    );
  }
}
