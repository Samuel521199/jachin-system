import { NextRequest, NextResponse } from "next/server";
import { getSupabase, isSupabaseConfigured } from "@/lib/supabase";
import { createClient } from "@/lib/supabase-auth/server";

/**
 * POST /api/v1/fleet/deploy
 * 批量下发蓝图 - 更新多台 edge_agents 的 current_blueprint_id
 */
export async function POST(req: NextRequest) {
  try {
    const body = await req.json().catch(() => ({}));
    const { agent_ids, blueprint_id } = body;

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

    if (!isSupabaseConfigured()) {
      // Mock: simulate success for demo
      return NextResponse.json({
        success: true,
        updated_count: agent_ids.length,
      });
    }

    const authClient = await createClient();
    const { data: { user } } = await authClient?.getUser() ?? { data: { user: null } };
    const userId = user?.id;

    const sb = getSupabase()!;

    // Verify blueprint exists
    const { data: bp, error: bpErr } = await sb
      .from("blueprints")
      .select("id")
      .eq("id", blueprint_id)
      .maybeSingle();

    if (bpErr || !bp) {
      return NextResponse.json(
        { success: false, error: "Blueprint not found" },
        { status: 404 }
      );
    }

    // Build query: update agents that belong to current user (or all if no auth)
    let query = sb
      .from("edge_agents")
      .update({
        current_blueprint_id: blueprint_id,
        updated_at: new Date().toISOString(),
      })
      .in("id", agent_ids);

    if (userId) {
      query = query.eq("user_id", userId);
    }

    const { data, error } = await query.select("id");

    if (error) {
      console.error("[fleet/deploy] Update error:", error);
      return NextResponse.json(
        { success: false, error: error.message },
        { status: 500 }
      );
    }

    const updatedCount = data?.length ?? 0;

    return NextResponse.json({
      success: true,
      updated_count: updatedCount,
    });
  } catch (e) {
    console.error("[fleet/deploy] Error:", e);
    return NextResponse.json(
      { success: false, error: "Bulk deploy failed" },
      { status: 500 }
    );
  }
}
