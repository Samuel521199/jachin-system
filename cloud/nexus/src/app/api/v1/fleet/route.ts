import { NextResponse } from "next/server";
import { auth } from "@/auth";
import { getDb, isDatabaseConfigured } from "@/db";
import { jsonOrgRequiredResponse } from "@/lib/org-session-guard";
import { edgeAgents, blueprints } from "@/db/schema";
import { eq, inArray, desc, and, or, isNull } from "drizzle-orm";

/**
 * GET /api/v1/fleet
 * 舰队指挥大屏 - 拉取当前登录用户名下的 edge_agents（含蓝图名称）
 */
export async function GET() {
  try {
    if (!isDatabaseConfigured()) {
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

    /** 仅本人节点；按当前会话组织收敛（与切换工作区一致） */
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
        currentBlueprintId: edgeAgents.currentBlueprintId,
      })
      .from(edgeAgents)
      .where(whereClause)
      .orderBy(desc(edgeAgents.lastHeartbeat));

    const bpIds = [...new Set(agents.map((a) => a.currentBlueprintId).filter(Boolean))] as string[];
    let blueprintMap: Record<string, string> = {};
    if (bpIds.length > 0) {
      const bps = await db
        .select({ id: blueprints.id, name: blueprints.name })
        .from(blueprints)
        .where(inArray(blueprints.id, bpIds));
      blueprintMap = Object.fromEntries(bps.map((b) => [b.id, b.name ?? ""]));
    }

    const allBps = await db
      .select({ id: blueprints.id, name: blueprints.name })
      .from(blueprints)
      .orderBy(desc(blueprints.createdAt))
      .limit(50);

    const agentsWithBlueprint = agents.map((a) => ({
      id: a.id,
      name: a.name ?? `边缘智能体-${a.id.slice(0, 8)}`,
      status: a.status,
      last_heartbeat: a.lastHeartbeat?.toISOString() ?? null,
      current_blueprint_id: a.currentBlueprintId,
      blueprint_name: a.currentBlueprintId
        ? blueprintMap[a.currentBlueprintId] ?? "—"
        : "—",
    }));

    const onlineCount = agentsWithBlueprint.filter((a) => a.status === "active").length;
    const offlineCount = agentsWithBlueprint.filter((a) => a.status === "offline").length;
    const staleCount = agentsWithBlueprint.filter((a) => {
      if (a.status !== "active") return false;
      const hb = a.last_heartbeat ? new Date(a.last_heartbeat).getTime() : 0;
      return Date.now() - hb > 120000;
    }).length;

    return NextResponse.json({
      agents: agentsWithBlueprint,
      blueprints: allBps.map((b) => ({ id: b.id, name: b.name })),
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
