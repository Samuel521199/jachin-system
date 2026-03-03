import { NextRequest, NextResponse } from "next/server";
import { getSupabase, isSupabaseConfigured } from "@/lib/supabase";

/**
 * POST /api/v1/agents/heartbeat
 * 边缘智能体心跳 - 支持 edge_agents 表
 * Body: { instance_id?, core_version?, metrics?, active_plugins? }
 * Headers: Authorization: Bearer <access_token>
 * 响应可包含当前分配的蓝图，供 Layer 2 拉取 AST
 */
export async function POST(req: NextRequest) {
  try {
    const authHeader = req.headers.get("Authorization");
    if (!authHeader?.startsWith("Bearer ")) {
      return NextResponse.json(
        { error: "防线拦截：未出示边缘智能体通行证" },
        { status: 401 }
      );
    }
    const token = authHeader.slice(7).trim();

    const body = await req.json().catch(() => ({}));
    const { instance_id, core_version, metrics, active_plugins } = body;

    if (!isSupabaseConfigured()) {
      return NextResponse.json(
        { success: true, timestamp: Date.now(), status: "ok" },
        { status: 200 }
      );
    }

    const sb = getSupabase()!;

    // 校验 edge_agents：auth_token 或 id 匹配
    let agent: { id: string; name: string | null; auth_token: string | null; current_blueprint_id: string | null } | null = null;
    const { data: byToken, error: errToken } = await sb
      .from("edge_agents")
      .select("id, name, auth_token, current_blueprint_id")
      .eq("auth_token", token)
      .eq("status", "active")
      .maybeSingle();
    if (!errToken && byToken) {
      agent = byToken;
    } else {
      const { data: byId, error: errId } = await sb
        .from("edge_agents")
        .select("id, name, auth_token, current_blueprint_id")
        .eq("id", token)
        .eq("status", "active")
        .maybeSingle();
      if (!errId && byId) agent = byId;
    }
    if (!agent) {
      return NextResponse.json(
        {
          error: "防线拦截：边缘智能体身份验证失败，拒绝接入大盘！",
        },
        { status: 403 }
      );
    }

    const agentId = agent.id;

    // 更新 last_heartbeat
    const { error: updateErr } = await sb
      .from("edge_agents")
      .update({
        last_heartbeat: new Date().toISOString(),
      })
      .eq("id", agentId);

    if (updateErr) {
      console.error("[agents/heartbeat] Update error:", updateErr);
    }

    // 若有关联蓝图，查询并返回供 Layer 2 拉取
    let blueprint: { name: string; ast_json: unknown } | null = null;
    const bpId = agent.current_blueprint_id;
    if (bpId) {
      const { data: bp } = await sb
        .from("blueprints")
        .select("name, ast_json")
        .eq("id", bpId)
        .maybeSingle();
      if (bp?.name && bp?.ast_json) {
        blueprint = { name: bp.name, ast_json: bp.ast_json };
      }
    }

    // 查询待下发的 inbound 消息（IM 网关：用户通过 TG/飞书发来的指令）
    const { data: pendingMessages } = await sb
      .from("agent_message_queue")
      .select("id, message_text, source_meta")
      .eq("agent_id", agentId)
      .eq("direction", "inbound")
      .eq("status", "pending")
      .order("created_at", { ascending: true })
      .limit(10);

    const task =
      pendingMessages && pendingMessages.length > 0
        ? pendingMessages.map((m) => m.message_text).join("\n")
        : null;

    return NextResponse.json({
      success: true,
      timestamp: Date.now(),
      status: "ok",
      ...(blueprint && { blueprint }),
      ...(task && {
        task,
        pending_message_ids: pendingMessages!.map((m) => m.id),
      }),
    });
  } catch (e) {
    console.error("[agents/heartbeat] Error:", e);
    return NextResponse.json(
      { error: "心跳处理失败" },
      { status: 500 }
    );
  }
}
