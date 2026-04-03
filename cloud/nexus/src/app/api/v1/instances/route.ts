import { NextResponse } from "next/server";
import { auth } from "@/auth";
import { getDb, isDatabaseConfigured } from "@/db";
import { jsonOrgRequiredResponse } from "@/lib/org-session-guard";
import { edgeAgents } from "@/db/schema";
import { inArray, desc, and, eq, or, isNull } from "drizzle-orm";

/**
 * GET /api/v1/instances
 * 指挥台 / Market 等 - 拉取当前登录用户名下的边缘智能体（与 /api/v1/fleet 隔离规则一致）
 */
export async function GET() {
  try {
    if (isDatabaseConfigured()) {
      const session = await auth();
      const userId = session?.user?.id;
      if (!userId) {
        return NextResponse.json(
          { error: "请先登录" },
          { status: 401 }
        );
      }

      const activeOrgId =
        typeof session.user?.orgId === "string" ? session.user.orgId.trim() : "";
      if (!activeOrgId) {
        return jsonOrgRequiredResponse();
      }

      const db = getDb()!;
      const tenantScope = or(
        isNull(edgeAgents.organizationId),
        eq(edgeAgents.organizationId, activeOrgId)
      );

      const whereClause = and(
        inArray(edgeAgents.status, ["active", "offline", "pending"]),
        eq(edgeAgents.userId, userId),
        tenantScope
      );

      const agents = await db
        .select({
          id: edgeAgents.id,
          name: edgeAgents.name,
          status: edgeAgents.status,
          lastHeartbeat: edgeAgents.lastHeartbeat,
        })
        .from(edgeAgents)
        .where(whereClause)
        .orderBy(desc(edgeAgents.lastHeartbeat));

      const instances = agents.map((a) => ({
        instance_id: a.id,
        name: a.name ?? `边缘智能体-${a.id.slice(0, 8)}`,
        status: a.status,
        last_heartbeat: a.lastHeartbeat?.toISOString() ?? null,
      }));

      return NextResponse.json({ instances });
    }

    // 无数据库：演示用 mock
    const mockData = [
      {
        instance_id: "dev-layer2-instance-001",
        core_version: "1.0.0",
        last_heartbeat: new Date().toISOString(),
        metrics: {
          cpu_percent: 12.5,
          ram_used_mb: 512,
          ram_total_mb: 4096,
        },
        active_plugins: {
          "core-vad-audio": "running",
          "heavy-tts": "restarting",
        },
      },
      {
        instance_id: "prod-layer2-002",
        core_version: "0.9.8",
        last_heartbeat: new Date(Date.now() - 120000).toISOString(),
        metrics: {
          cpu_percent: 0,
          ram_used_mb: 0,
          ram_total_mb: 8192,
        },
        active_plugins: { "rag-memory": "stopped" },
      },
    ];

    return NextResponse.json({ instances: mockData });
  } catch (e) {
    console.error("Instances API Error:", e);
    return NextResponse.json(
      { error: "获取边缘智能体列表失败" },
      { status: 500 }
    );
  }
}
