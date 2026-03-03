import { NextResponse } from "next/server";
import { getSupabase, isSupabaseConfigured } from "@/lib/supabase";
import { createClient } from "@/lib/supabase-auth/server";

/**
 * GET /api/v1/fleet
 * 舰队指挥大屏 - 拉取当前用户的 edge_agents，含蓝图名称
 */
export async function GET() {
  try {
    if (!isSupabaseConfigured()) {
      // Mock for demo
      const mockAgents = [
        { id: "dev-001", name: "dev-layer2-instance-001", status: "active", last_heartbeat: new Date().toISOString(), current_blueprint_id: null, blueprint_name: "—" },
        { id: "prod-002", name: "prod-layer2-002", status: "offline", last_heartbeat: new Date(Date.now() - 120000).toISOString(), current_blueprint_id: null, blueprint_name: "—" },
      ];
      return NextResponse.json({
        agents: mockAgents,
        blueprints: [{ id: "bp-1", name: "离线医疗助手" }, { id: "bp-2", name: "傲娇女仆客服" }],
        stats: { online: 1, offline: 1, stale: 0, total: 2 },
      });
    }

    const authClient = await createClient();
    const { data: { user } } = await authClient?.getUser() ?? { data: { user: null } };
    const userId = user?.id;

    const sb = getSupabase()!;

    // Fetch agents (optionally filter by user)
    let agentsQuery = sb
      .from("edge_agents")
      .select("id, name, status, last_heartbeat, current_blueprint_id, user_id")
      .in("status", ["active", "offline", "pending"])
      .order("last_heartbeat", { ascending: false, nullsFirst: false });

    if (userId) {
      agentsQuery = agentsQuery.eq("user_id", userId);
    }

    const { data: agents, error: agentsErr } = await agentsQuery;

    if (agentsErr) {
      console.error("[fleet] Agents fetch error:", agentsErr);
      return NextResponse.json(
        { error: agentsErr.message },
        { status: 500 }
      );
    }

    // Fetch blueprints for name lookup
    const bpIds = [...new Set((agents ?? []).map((a) => a.current_blueprint_id).filter(Boolean))];
    let blueprintMap: Record<string, string> = {};
    if (bpIds.length > 0) {
      const { data: bps } = await sb
        .from("blueprints")
        .select("id, name")
        .in("id", bpIds);
      blueprintMap = Object.fromEntries((bps ?? []).map((b) => [b.id, b.name ?? ""]));
    }

    // Fetch all blueprints for deploy dropdown
    const { data: allBps } = await sb
      .from("blueprints")
      .select("id, name")
      .order("created_at", { ascending: false })
      .limit(50);

    const agentsWithBlueprint = (agents ?? []).map((a) => ({
      id: a.id,
      name: a.name ?? `边缘智能体-${a.id.slice(0, 8)}`,
      status: a.status,
      last_heartbeat: a.last_heartbeat,
      current_blueprint_id: a.current_blueprint_id,
      blueprint_name: a.current_blueprint_id
        ? blueprintMap[a.current_blueprint_id] ?? "—"
        : "—",
    }));

    const onlineCount = agentsWithBlueprint.filter((a) => a.status === "active").length;
    const offlineCount = agentsWithBlueprint.filter((a) => a.status === "offline").length;
    const staleCount = agentsWithBlueprint.filter((a) => {
      if (a.status !== "active") return false;
      const hb = a.last_heartbeat ? new Date(a.last_heartbeat).getTime() : 0;
      return Date.now() - hb > 120000; // 2 min
    }).length;

    return NextResponse.json({
      agents: agentsWithBlueprint,
      blueprints: (allBps ?? []).map((b) => ({ id: b.id, name: b.name })),
      stats: {
        online: onlineCount,
        offline: offlineCount,
        stale: staleCount,
        total: agentsWithBlueprint.length,
      },
    });
  } catch (e) {
    console.error("[fleet] Error:", e);
    return NextResponse.json(
      { error: "获取舰队数据失败" },
      { status: 500 }
    );
  }
}
