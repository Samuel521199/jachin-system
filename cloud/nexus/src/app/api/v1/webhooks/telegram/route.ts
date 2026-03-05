import { NextRequest, NextResponse } from "next/server";
import { getDb, isDatabaseConfigured } from "@/db";
import { edgeAgents, agentMessageQueue } from "@/db/schema";
import { eq, and } from "drizzle-orm";

/**
 * POST /api/v1/webhooks/telegram
 * Telegram 机器人 Webhook 入口
 */
export async function POST(req: NextRequest) {
  try {
    const body = await req.json().catch(() => ({}));
    const message = body.message ?? body.edited_message;
    if (!message?.chat?.id || !message.text) {
      return NextResponse.json({ ok: true });
    }

    const chatId = String(message.chat.id);
    const text = (message.text || "").trim();
    if (!text) return NextResponse.json({ ok: true });

    if (!isDatabaseConfigured()) {
      console.warn("[webhooks/telegram] Database not configured, message dropped");
      return NextResponse.json({ ok: true });
    }

    const db = getDb()!;

    const [agent] = await db
      .select({ id: edgeAgents.id })
      .from(edgeAgents)
      .where(
        and(
          eq(edgeAgents.imBindingId, chatId),
          eq(edgeAgents.imPlatform, "telegram"),
          eq(edgeAgents.status, "active")
        )
      )
      .limit(1);

    if (!agent) {
      console.warn("[webhooks/telegram] No agent bound to chat_id:", chatId);
      return NextResponse.json({ ok: true });
    }

    await db.insert(agentMessageQueue).values({
      agentId: agent.id,
      messageText: text,
      direction: "inbound",
      status: "pending",
      sourceMeta: {
        telegram_chat_id: chatId,
        message_id: message.message_id,
        from: message.from?.id,
      },
    });

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
