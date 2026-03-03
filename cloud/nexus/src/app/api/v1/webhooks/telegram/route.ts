import { NextRequest, NextResponse } from "next/server";
import { getSupabase, isSupabaseConfigured } from "@/lib/supabase";

/**
 * POST /api/v1/webhooks/telegram
 * Telegram 机器人 Webhook 入口
 *
 * 配置：在 BotFather 设置 setWebhook 指向 https://your-domain/api/v1/webhooks/telegram
 * 环境变量：TELEGRAM_BOT_TOKEN（可选，用于验证来源）
 *
 * 流程：
 * 1. 解析 Telegram 发来的 message，提取 chat_id、text
 * 2. 根据 chat_id 查 edge_agents.im_binding_id，找到绑定的 agent_id
 * 3. 将消息插入 agent_message_queue (direction: inbound, status: pending)
 * 4. 边缘 Agent 下次心跳时会拉取该消息并执行
 */
export async function POST(req: NextRequest) {
  try {
    const body = await req.json().catch(() => ({}));
    const message = body.message ?? body.edited_message;
    if (!message?.chat?.id || !message.text) {
      return NextResponse.json({ ok: true }); // Telegram 要求 200，否则会重试
    }

    const chatId = String(message.chat.id);
    const text = (message.text || "").trim();
    if (!text) return NextResponse.json({ ok: true });

    if (!isSupabaseConfigured()) {
      console.warn("[webhooks/telegram] Supabase not configured, message dropped");
      return NextResponse.json({ ok: true });
    }

    const sb = getSupabase()!;

    // 根据 chat_id 查找绑定的 agent
    const { data: agent, error: agentErr } = await sb
      .from("edge_agents")
      .select("id")
      .eq("im_binding_id", chatId)
      .eq("im_platform", "telegram")
      .eq("status", "active")
      .maybeSingle();

    if (agentErr || !agent) {
      console.warn("[webhooks/telegram] No agent bound to chat_id:", chatId);
      return NextResponse.json({ ok: true });
    }

    // 插入消息队列
    const { error: insertErr } = await sb.from("agent_message_queue").insert({
      agent_id: agent.id,
      message_text: text,
      direction: "inbound",
      status: "pending",
      source_meta: {
        telegram_chat_id: chatId,
        message_id: message.message_id,
        from: message.from?.id,
      },
    });

    if (insertErr) {
      console.error("[webhooks/telegram] Insert error:", insertErr);
      return NextResponse.json({ ok: true });
    }

    return NextResponse.json({ ok: true });
  } catch (e) {
    console.error("[webhooks/telegram] Error:", e);
    return NextResponse.json({ ok: true });
  }
}

/*
 * ========== 飞书 (Lark) 接入逻辑说明 ==========
 *
 * 1. 创建飞书应用，获取 App ID、App Secret
 * 2. 启用「接收消息」能力，配置请求地址：POST /api/v1/webhooks/lark
 * 3. 飞书事件格式：
 *    - 消息事件：event.message.message_type=text，event.message.content 含 JSON
 *    - 解析 event.message.chat_id 或 event.sender.sender_id
 * 4. 绑定：用户在 Console 绑定飞书时，将 chat_id 写入 edge_agents.im_binding_id，im_platform='lark'
 * 5. 路由逻辑与 Telegram 类似：chat_id -> agent_id -> agent_message_queue
 *
 * 示例 route.ts 结构：
 *   const chatId = event.message?.chat_id ?? event.sender?.sender_id?.open_id;
 *   const text = JSON.parse(event.message?.content || '{}').text;
 *   ... 同上查 agent、插入队列
 */
