import { NextRequest, NextResponse } from "next/server";
import { getDb, isDatabaseConfigured } from "@/db";
import { edgeAgents, agentMessageQueue } from "@/db/schema";
import { eq, and, inArray } from "drizzle-orm";

/**
 * POST /api/v1/agents/result
 * 边缘 Agent 执行结果回传
 *
 * Headers: Authorization: Bearer <access_token>
 * Body: { result: string, message_ids?: string[] }
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

    if (!isDatabaseConfigured()) {
      return NextResponse.json(
        { success: false, error: "服务未配置" },
        { status: 503 }
      );
    }

    const db = getDb()!;

    // 校验 Agent
    let agentRow: { id: string; imBindingId: string | null; imPlatform: string | null } | null = null;
    const byToken = await db
      .select({ id: edgeAgents.id, imBindingId: edgeAgents.imBindingId, imPlatform: edgeAgents.imPlatform })
      .from(edgeAgents)
      .where(and(eq(edgeAgents.authToken, token), eq(edgeAgents.status, "active")))
      .limit(1);
    if (byToken.length > 0) agentRow = byToken[0];
    else {
      const byId = await db
        .select({ id: edgeAgents.id, imBindingId: edgeAgents.imBindingId, imPlatform: edgeAgents.imPlatform })
        .from(edgeAgents)
        .where(and(eq(edgeAgents.id, token), eq(edgeAgents.status, "active")))
        .limit(1);
      if (byId.length > 0) agentRow = byId[0];
    }

    if (!agentRow) {
      return NextResponse.json(
        { error: "身份验证失败" },
        { status: 403 }
      );
    }

    // 标记消息为已处理
    if (Array.isArray(message_ids) && message_ids.length > 0) {
      await db
        .update(agentMessageQueue)
        .set({ status: "processed", processedAt: new Date() })
        .where(
          and(
            eq(agentMessageQueue.agentId, agentRow.id),
            inArray(agentMessageQueue.id, message_ids)
          )
        );
    }

    // 若已绑定 IM，将结果推回用户
    if (agentRow.imBindingId && resultText) {
      const platform = agentRow.imPlatform || "telegram";
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
                  chat_id: agentRow.imBindingId,
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
