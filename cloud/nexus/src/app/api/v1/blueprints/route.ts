import { NextResponse } from "next/server";
import { getSupabase, isSupabaseConfigured } from "@/lib/supabase";

/**
 * GET /api/v1/blueprints
 * 蓝图武库 - 拉取已铸造的蓝图列表
 */
export async function GET() {
  try {
    if (!isSupabaseConfigured()) {
      return NextResponse.json({ blueprints: [] });
    }

    const sb = getSupabase()!;
    const { data, error } = await sb
      .from("blueprints")
      .select("id, name, description, created_at")
      .order("created_at", { ascending: false })
      .limit(50);

    if (error) {
      console.error("[blueprints] Fetch error:", error);
      return NextResponse.json(
        { error: error.message },
        { status: 500 }
      );
    }

    return NextResponse.json({
      blueprints: (data ?? []).map((b) => ({
        id: b.id,
        name: b.name,
        description: b.description ?? "",
      })),
    });
  } catch (e) {
    console.error("[blueprints] Error:", e);
    return NextResponse.json(
      { error: "获取蓝图列表失败" },
      { status: 500 }
    );
  }
}
