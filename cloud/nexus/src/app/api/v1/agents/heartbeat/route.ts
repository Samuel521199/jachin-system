import { NextRequest, NextResponse } from "next/server";
import { getDb, isDatabaseConfigured } from "@/db";
import { edgeAgents, blueprints, agentMessageQueue } from "@/db/schema";
import { eq, and, asc } from "drizzle-orm";

const UUID_REGEX = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * POST /api/v1/agents/heartbeat
 * 边缘智能体心跳 - 支持 edge_agents 表
 * Body: { instance_id?, core_version?, metrics?, active_plugins? }
 * Headers: Authorization: Bearer <access_token>
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

    await req.json().catch(() => ({}));

    if (!isDatabaseConfigured()) {
      return NextResponse.json(
        { success: true, timestamp: Date.now(), status: "ok" },
        { status: 200 }
      );
    }

    const db = getDb()!;

    // 校验：auth_token 或 id 匹配，接受 active 与 offline
    // 注意：id 为 UUID 类型，仅当 token 为 UUID 格式时才按 id 查询，避免 "jch-mock-xxx" 等旧凭证导致类型错误
    let agentRow: { id: string; status: string; currentBlueprintId: string | null } | null = null;
    const byToken = await db
      .select({ id: edgeAgents.id, status: edgeAgents.status, currentBlueprintId: edgeAgents.currentBlueprintId })
      .from(edgeAgents)
      .where(
        and(
          eq(edgeAgents.authToken, token),
          eq(edgeAgents.status, "active")
        )
      )
      .limit(1);
    if (byToken.length > 0) agentRow = byToken[0];
    else if (UUID_REGEX.test(token)) {
      const byId = await db
        .select({ id: edgeAgents.id, status: edgeAgents.status, currentBlueprintId: edgeAgents.currentBlueprintId })
        .from(edgeAgents)
        .where(
          and(
            eq(edgeAgents.id, token),
            eq(edgeAgents.status, "active")
          )
        )
        .limit(1);
      if (byId.length > 0) agentRow = byId[0];
    }

    if (!agentRow) {
      const byOffline = await db
        .select({ id: edgeAgents.id, status: edgeAgents.status, currentBlueprintId: edgeAgents.currentBlueprintId })
        .from(edgeAgents)
        .where(eq(edgeAgents.authToken, token))
        .limit(1);
      if (byOffline.length > 0) agentRow = byOffline[0];
      else if (UUID_REGEX.test(token)) {
        const byIdOffline = await db
          .select({ id: edgeAgents.id, status: edgeAgents.status, currentBlueprintId: edgeAgents.currentBlueprintId })
          .from(edgeAgents)
          .where(eq(edgeAgents.id, token))
          .limit(1);
        if (byIdOffline.length > 0) agentRow = byIdOffline[0];
      }
    }

    if (!agentRow) {
      const hint = token.startsWith("jch-mock-")
        ? "（检测到旧版内存模式凭证，请重新执行 run-pair.ps1 完成配对）"
        : "";
      return NextResponse.json(
        { error: `防线拦截：边缘智能体身份验证失败，拒绝接入大盘！${hint}` },
        { status: 403 }
      );
    }

    const agentId = agentRow.id;

    // 更新 last_heartbeat，若为 offline 则置为 active
    await db
      .update(edgeAgents)
      .set({
        lastHeartbeat: new Date(),
        ...(agentRow.status === "offline" ? { status: "active" } : {}),
      })
      .where(eq(edgeAgents.id, agentId));

    // 查询蓝图（API 返回 snake_case）
    let blueprint: { name: string; ast_json: unknown } | null = null;
    const bpId = agentRow.currentBlueprintId;
    if (bpId) {
      const [bp] = await db
        .select({ name: blueprints.name, astJson: blueprints.astJson })
        .from(blueprints)
        .where(eq(blueprints.id, bpId))
        .limit(1);
      if (bp?.name && bp?.astJson) {
        blueprint = { name: bp.name, ast_json: bp.astJson };
      }
    }

    // 查询待下发的 inbound 消息
    const pendingMessages = await db
      .select({ id: agentMessageQueue.id, messageText: agentMessageQueue.messageText })
      .from(agentMessageQueue)
      .where(
        and(
          eq(agentMessageQueue.agentId, agentId),
          eq(agentMessageQueue.direction, "inbound"),
          eq(agentMessageQueue.status, "pending")
        )
      )
      .orderBy(asc(agentMessageQueue.createdAt))
      .limit(10);

    const task =
      pendingMessages.length > 0
        ? pendingMessages.map((m) => m.messageText).join("\n")
        : null;

    return NextResponse.json({
      success: true,
      timestamp: Date.now(),
      status: "ok",
      ...(blueprint && { blueprint }),
      ...(task && {
        task,
        pending_message_ids: pendingMessages.map((m) => m.id),
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
