import { NextRequest, NextResponse } from "next/server";
import { getSupabase, isSupabaseConfigured } from "@/lib/supabase";

/**
 * POST /api/v1/agents/result
 * 边缘 Agent 执行结果回传
 *
 * Headers: Authorization: Bearer <access_token>
 * Body: { result: string, message_ids?: string[] }
 *
 * 流程：
 * 1. 校验 Agent 身份
 * 2. 若有 message_ids，标记对应队列消息为 processed
 * 3. 若 Agent 已绑定 IM (im_binding_id)，调用 Telegram/飞书 API 将 result 发回用户手机
 */
export async function POST(req: NextRequest) {
  try {
    const authHeader = req.headers.get("Authorization");
    if (!authHeader?.startsWith("Bearer ")) {
      return NextResponse.json(
        { error: "未出示边缘智能体通行证" },
        { status: 401 }
      );
    }
    const token = authHeader.slice(7).trim();

    const body = await req.json().catch(() => ({}));
    const { result, message_ids } = body;
    const resultText = typeof result === "string" ? result : String(result ?? "");

    if (!isSupabaseConfigured()) {
      return NextResponse.json(
        { success: false, error: "服务未配置" },
        { status: 503 }
      );
    }

    const sb = getSupabase()!;

    // 校验 Agent
    let agent: { id: string; im_binding_id: string | null; im_platform: string | null } | null = null;
    const { data: byToken } = await sb
      .from("edge_agents")
      .select("id, im_binding_id, im_platform")
      .eq("auth_token", token)
      .eq("status", "active")
      .maybeSingle();
    if (byToken) agent = byToken;
    else {
      const { data: byId } = await sb
        .from("edge_agents")
        .select("id, im_binding_id, im_platform")
        .eq("id", token)
        .eq("status", "active")
        .maybeSingle();
      if (byId) agent = byId;
    }

    if (!agent) {
      return NextResponse.json(
        { error: "身份验证失败" },
        { status: 403 }
      );
    }

    // 标记消息为已处理
    if (Array.isArray(message_ids) && message_ids.length > 0) {
      await sb
        .from("agent_message_queue")
        .update({ status: "processed", processed_at: new Date().toISOString() })
        .eq("agent_id", agent.id)
        .in("id", message_ids);
    }

    // 若已绑定 IM，将结果推回用户
    if (agent.im_binding_id && resultText) {
      const platform = agent.im_platform || "telegram";
      if (platform === "telegram") {
        const botToken = process.env.TELEGRAM_BOT_TOKEN;
        if (botToken) {
          try {
            const res = await fetch(
              `https://api.telegram.org/bot${botToken}/sendMessage`,
              {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                  chat_id: agent.im_binding_id,
                  text: resultText.slice(0, 4096),
                }),
              }
            );
            if (!res.ok) {
              console.error("[agents/result] Telegram sendMessage failed:", await res.text());
            }
          } catch (e) {
            console.error("[agents/result] Telegram API error:", e);
          }
        }
      }
      // 飞书：platform === 'lark' 时调用飞书 sendMessage API，逻辑类似
    }

    return NextResponse.json({ success: true });
  } catch (e) {
    console.error("[agents/result] Error:", e);
    return NextResponse.json(
      { error: "结果回传失败" },
      { status: 500 }
    );
  }
}
