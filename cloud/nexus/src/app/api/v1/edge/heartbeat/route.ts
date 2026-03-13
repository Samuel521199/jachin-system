import { NextResponse } from "next/server";
import { getDb, isDatabaseConfigured } from "@/db";
import { error as logError } from "@/lib/console-utc";
import { edgeAgents } from "@/db/schema";
import { eq, or, and } from "drizzle-orm";

const UUID_REGEX = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * POST /api/v1/edge/heartbeat
 * L2 策略同步心跳 - 拉取订阅状态与全局安全策略
 *
 * Body: { instance_id?, core_version? }
 * Headers: Authorization: Bearer <access_token>
 *
 * Response: { success, subscription_status?, global_banned_skills? }
 * - subscription_status: "active" | "expired" | "trial" | "suspended"
 * - global_banned_skills: ["core:shell_exec", ...] 全局封禁技能
 */
export async function POST(req: Request) {
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
    void body; // instance_id, core_version reserved for future use

    if (!isDatabaseConfigured()) {
      return NextResponse.json({
        success: true,
        timestamp: Date.now(),
        subscription_status: "active",
        global_banned_skills: [],
      });
    }

    const db = getDb()!;

    try {
      // id 列为 UUID 类型，仅当 token 为有效 UUID 时才能比较，否则 PostgreSQL 报错
      const isUuid = UUID_REGEX.test(token);
      const whereClause = isUuid
        ? or(
            and(eq(edgeAgents.authToken, token), eq(edgeAgents.status, "active")),
            and(eq(edgeAgents.id, token), eq(edgeAgents.status, "active"))
          )
        : and(eq(edgeAgents.authToken, token), eq(edgeAgents.status, "active"));

      const [agent] = await db
        .select({ id: edgeAgents.id, status: edgeAgents.status })
        .from(edgeAgents)
        .where(whereClause)
        .limit(1);

      let agentRow = agent;
      if (!agentRow && isUuid) {
        const [byId] = await db
          .select({ id: edgeAgents.id, status: edgeAgents.status })
          .from(edgeAgents)
          .where(eq(edgeAgents.id, token))
          .limit(1);
        agentRow = byId;
      }
      if (!agentRow) {
        const [byToken] = await db
          .select({ id: edgeAgents.id, status: edgeAgents.status })
          .from(edgeAgents)
          .where(eq(edgeAgents.authToken, token))
          .limit(1);
        agentRow = byToken;
      }

      if (!agentRow) {
        return NextResponse.json(
          { error: "边缘智能体身份验证失败" },
          { status: 403 }
        );
      }

      await db
        .update(edgeAgents)
        .set({
          lastHeartbeat: new Date(),
          ...(agentRow.status === "offline" ? { status: "active" } : {}),
        })
        .where(eq(edgeAgents.id, agentRow.id));

      // TODO: 从 organization 或 subscriptions 表读取真实订阅状态
      const subscription_status = "active";
      const global_banned_skills: string[] = [];

      return NextResponse.json({
        success: true,
        timestamp: Date.now(),
        subscription_status,
        global_banned_skills,
      });
    } catch (dbError) {
      // 表不存在或 schema 不匹配时：降级返回成功，避免阻塞 L2 心跳
      logError("[edge/heartbeat] DB error (fallback to mock):", dbError);
      return NextResponse.json({
        success: true,
        timestamp: Date.now(),
        subscription_status: "active",
        global_banned_skills: [],
      });
    }
  } catch (e) {
    logError("[edge/heartbeat] Error:", e);
    return NextResponse.json(
      { error: "心跳处理失败" },
      { status: 500 }
    );
  }
}
