import { NextResponse } from "next/server";
import { getSupabase, isSupabaseConfigured } from "@/lib/supabase";

/**
 * GET /api/v1/instances
 * 舰队指挥台 - 拉取 Layer 2 边缘智能体列表
 */
export async function GET() {
  try {
    if (isSupabaseConfigured()) {
      const sb = getSupabase()!;
      const { data, error } = await sb
        .from("layer2_instances")
        .select("instance_id, core_version, last_heartbeat, metrics, active_plugins")
        .order("last_heartbeat", { ascending: false });

      if (error) {
        console.error("Instances fetch error:", error);
        return NextResponse.json(
          { error: error.message },
          { status: 500 }
        );
      }
      return NextResponse.json({ instances: data ?? [] });
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
