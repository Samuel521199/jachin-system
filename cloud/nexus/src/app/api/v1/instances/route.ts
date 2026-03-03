import { NextResponse } from "next/server";
import { getSupabase, isSupabaseConfigured } from "@/lib/supabase";

/**
 * GET /api/v1/instances
 * 舰队指挥台 - 拉取边缘智能体列表（优先 edge_agents，回退 layer2_instances）
 */
export async function GET() {
  try {
    if (isSupabaseConfigured()) {
      const sb = getSupabase()!;

      try {
        const { data: agents, error: agentsErr } = await sb
          .from("edge_agents")
          .select("id, name, status, last_heartbeat, current_blueprint_id")
          .in("status", ["active", "offline", "pending"])
          .order("last_heartbeat", { ascending: false, nullsFirst: false });

        if (!agentsErr && agents && agents.length > 0) {
          const instances = agents.map((a: { id: string; name: string | null; status: string; last_heartbeat: string | null }) => ({
            instance_id: a.id,
            name: a.name ?? `边缘智能体-${a.id.slice(0, 8)}`,
            status: a.status,
            last_heartbeat: a.last_heartbeat,
          }));
          return NextResponse.json({ instances });
        }
      } catch {
        // edge_agents 表可能尚未迁移，回退到 layer2_instances
      }

      // 回退：layer2_instances（兼容旧数据）
      const { data: legacy, error } = await sb
        .from("layer2_instances")
        .select("id, instance_id, core_version, last_heartbeat, metrics, active_plugins")
        .order("last_heartbeat", { ascending: false, nullsFirst: false });

      if (error) {
        console.error("Instances fetch error:", error);
        return NextResponse.json(
          { error: error.message },
          { status: 500 }
        );
      }

      const instances = (legacy ?? []).map((l: { id: string; instance_id: string; core_version?: string; last_heartbeat?: string; metrics?: unknown; active_plugins?: unknown }) => ({
        instance_id: l.instance_id,
        core_version: l.core_version,
        last_heartbeat: l.last_heartbeat,
        metrics: l.metrics,
        active_plugins: l.active_plugins,
      }));
      return NextResponse.json({ instances });
    }

    // Mock 数据，方便立刻看到 UI 效果
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
