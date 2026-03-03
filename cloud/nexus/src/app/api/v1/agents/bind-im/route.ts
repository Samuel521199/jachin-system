import { NextRequest, NextResponse } from "next/server";
import { getSupabase, isSupabaseConfigured } from "@/lib/supabase";
import { createClient } from "@/lib/supabase-auth/server";

/**
 * POST /api/v1/agents/bind-im
 * 绑定 IM（Telegram / 飞书）到边缘智能体
 *
 * Body: { agent_id: string, im_binding_id: string, im_platform?: 'telegram' | 'lark' }
 *
 * 用户需已登录，且 agent 属于当前用户。
 * im_binding_id: Telegram 为 chat_id（与 @userinfobot 对话获取），飞书为 chat_id
 */
export async function POST(req: NextRequest) {
  try {
    const body = await req.json().catch(() => ({}));
    const { agent_id, im_binding_id, im_platform } = body;

    if (!agent_id || !im_binding_id || typeof im_binding_id !== "string") {
      return NextResponse.json(
        { success: false, error: "agent_id and im_binding_id required" },
        { status: 400 }
      );
    }

    const platform = (im_platform || "telegram") as string;
    if (!["telegram", "lark"].includes(platform)) {
      return NextResponse.json(
        { success: false, error: "im_platform must be telegram or lark" },
        { status: 400 }
      );
    }

    if (!isSupabaseConfigured()) {
      return NextResponse.json(
        { success: false, error: "Supabase not configured" },
        { status: 503 }
      );
    }

    const authClient = await createClient();
    const { data: { user } } = (await authClient?.getUser()) ?? { data: { user: null } };
    const userId = user?.id;

    const sb = getSupabase()!;

    let query = sb
      .from("edge_agents")
      .update({
        im_binding_id: String(im_binding_id).trim(),
        im_platform: platform,
        updated_at: new Date().toISOString(),
      })
      .eq("id", agent_id);

    if (userId) {
      query = query.eq("user_id", userId);
    }

    const { error } = await query;

    if (error) {
      console.error("[agents/bind-im] Update error:", error);
      return NextResponse.json(
        { success: false, error: error.message },
        { status: 500 }
      );
    }

    return NextResponse.json({ success: true });
  } catch (e) {
    console.error("[agents/bind-im] Error:", e);
    return NextResponse.json(
      { error: "绑定失败" },
      { status: 500 }
    );
  }
}
