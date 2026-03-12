import { NextResponse } from "next/server";
import { getDb, isDatabaseConfigured } from "@/db";
import { edgeAgents } from "@/db/schema";
import { eq, or, and } from "drizzle-orm";

/**
 * POST /api/v1/instances/heartbeat
 * 端云心跳 - 使用 edge_agents 表（与 agents/heartbeat 共用鉴权逻辑）
 *
 * Body: { instance_id, core_version, metrics, active_plugins }
 * Headers: Authorization: Bearer <access_token>
 */
export async function POST(req: Request) {
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
    const { instance_id, metrics, active_plugins } = body;

    if (!isDatabaseConfigured()) {
      return NextResponse.json({ success: true, timestamp: Date.now() });
    }

    const db = getDb()!;

    // 鉴权：auth_token 或 id 匹配
    const [agent] = await db
      .select({ id: edgeAgents.id })
      .from(edgeAgents)
      .where(
        or(
          and(eq(edgeAgents.authToken, token), eq(edgeAgents.status, "active")),
          and(eq(edgeAgents.id, token), eq(edgeAgents.status, "active"))
        )
      )
      .limit(1);

    let agentId = agent?.id;
    if (!agent) {
      const [offlineAgent] = await db
        .select({ id: edgeAgents.id })
        .from(edgeAgents)
        .where(or(eq(edgeAgents.authToken, token), eq(edgeAgents.id, token)))
        .limit(1);

      if (!offlineAgent) {
        return NextResponse.json(
          { error: "防线拦截：边缘智能体身份验证失败，拒绝接入大盘！" },
          { status: 403 }
        );
      }

      agentId = offlineAgent.id;
      await db
        .update(edgeAgents)
        .set({ lastHeartbeat: new Date(), status: "active" })
        .where(eq(edgeAgents.id, offlineAgent.id));
    } else {
      await db
        .update(edgeAgents)
        .set({ lastHeartbeat: new Date() })
        .where(eq(edgeAgents.id, agent.id));
    }

    console.log(`\n🛸 收到边缘智能体 [${String(instance_id ?? agentId ?? "?").slice(0, 8)}] 心跳:`);
    console.log(`   💻 CPU: ${metrics?.cpu_percent ?? "?"}% | RAM: ${metrics?.ram_used_mb ?? "?"}MB`);
    console.log(`   📦 武器状态:`, active_plugins ?? {});

    return NextResponse.json({ success: true, timestamp: Date.now() });
  } catch (e) {
    console.error("Heartbeat Error:", e);
    return NextResponse.json(
      { error: "心跳处理失败" },
      { status: 500 }
    );
  }
}
