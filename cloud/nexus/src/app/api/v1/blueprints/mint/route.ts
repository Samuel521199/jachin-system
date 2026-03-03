import { NextRequest, NextResponse } from "next/server";
import { getSupabase, isSupabaseConfigured } from "@/lib/supabase";
import { createClient } from "@/lib/supabase-auth/server";

/**
 * POST /api/v1/blueprints/mint
 * Forge 蓝图铸造 - 将 React Flow AST 写入 blueprints 表
 * Body: { name, ast_json, description?, price? }
 */
export async function POST(req: NextRequest) {
  try {
    const body = await req.json().catch(() => ({}));
    const { name, ast_json, description, price } = body;

    if (!name || typeof name !== "string") {
      return NextResponse.json(
        { success: false, error: "Missing or invalid name" },
        { status: 400 }
      );
    }

    if (!ast_json || typeof ast_json !== "object") {
      return NextResponse.json(
        { success: false, error: "Missing or invalid ast_json" },
        { status: 400 }
      );
    }

    if (!isSupabaseConfigured()) {
      return NextResponse.json(
        { success: false, error: "Supabase 未配置，无法写入数据库" },
        { status: 503 }
      );
    }

    const sb = getSupabase()!;

    // 获取当前登录用户
    let creatorId: string | null = null;
    const authClient = await createClient();
    if (authClient) {
      const { data: { user } } = await authClient.getUser();
      if (user?.id) {
        creatorId = user.id;
      }
    }

    const { data: blueprint, error } = await sb
      .from("blueprints")
      .insert({
        creator_id: creatorId,
        name: String(name).trim(),
        description: description ? String(description).trim() : null,
        ast_json,
        price: typeof price === "number" ? price : 0,
      })
      .select("id, name, created_at")
      .single();

    if (error) {
      console.error("[blueprints/mint] Insert error:", error);
      return NextResponse.json(
        { success: false, error: error.message },
        { status: 500 }
      );
    }

    return NextResponse.json({
      success: true,
      message: "AST 语法树已成功写入底层数据库！版税资产已确权！",
      blueprint: {
        id: blueprint.id,
        name: blueprint.name,
        created_at: blueprint.created_at,
      },
    });
  } catch (e) {
    console.error("[blueprints/mint] Error:", e);
    return NextResponse.json(
      { success: false, error: (e as Error).message },
      { status: 500 }
    );
  }
}
