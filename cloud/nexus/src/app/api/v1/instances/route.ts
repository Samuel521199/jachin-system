import { NextResponse } from "next/server";
import { getDb, isDatabaseConfigured } from "@/db";
import { edgeAgents } from "@/db/schema";
import { inArray, desc } from "drizzle-orm";

/**
 * GET /api/v1/instances
 * 舰队指挥台 - 拉取边缘智能体列表
 */
export async function GET() {
  try {
    if (isDatabaseConfigured()) {
      const db = getDb()!;
      const agents = await db
        .select({
          id: edgeAgents.id,
          name: edgeAgents.name,
          status: edgeAgents.status,
          lastHeartbeat: edgeAgents.lastHeartbeat,
        })
        .from(edgeAgents)
        .where(inArray(edgeAgents.status, ["active", "offline", "pending"]))
        .orderBy(desc(edgeAgents.lastHeartbeat));

      if (agents.length > 0) {
        const instances = agents.map((a) => ({
          instance_id: a.id,
          name: a.name ?? `边缘智能体-${a.id.slice(0, 8)}`,
          status: a.status,
          last_heartbeat: a.lastHeartbeat?.toISOString() ?? null,
        }));
        return NextResponse.json({ instances });
      }
    }

    // Mock 数据
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
