import { NextResponse } from "next/server";
import { auth } from "@/auth";
import { getDb, isDatabaseConfigured } from "@/db";
import { edgeAgents } from "@/db/schema";
import { inArray, desc, and, eq, or, isNull } from "drizzle-orm";

export const dynamic = "force-dynamic";

function emptyInstances(reason: "auth_required" | "org_required") {
  return NextResponse.json({
    instances: [],
    success: true,
    reason,
  });
}

/**
 * GET /api/v1/instances
 * Read-only instance list used by console and market pages.
 *
 * When the database is configured, the list is still scoped by the current
 * authenticated user and active organization. If the browser is not logged in
 * yet, return an empty list instead of 401 so L1 startup and public pages do
 * not produce noisy auth errors.
 */
export async function GET() {
  try {
    if (isDatabaseConfigured()) {
      const session = await auth();
      const userId = session?.user?.id;
      if (!userId) {
        return emptyInstances("auth_required");
      }

      const activeOrgId =
        typeof session.user?.orgId === "string" ? session.user.orgId.trim() : "";
      if (!activeOrgId) {
        return emptyInstances("org_required");
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

      const instances = agents.map((agent) => ({
        instance_id: agent.id,
        name: agent.name ?? `Edge Agent ${agent.id.slice(0, 8)}`,
        status: agent.status,
        last_heartbeat: agent.lastHeartbeat?.toISOString() ?? null,
      }));

      return NextResponse.json({ instances, success: true });
    }

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

    return NextResponse.json({ instances: mockData, success: true });
  } catch (error) {
    console.error("[instances] Error:", error);
    return NextResponse.json(
      { success: false, error: "Failed to fetch instance list" },
      { status: 500 }
    );
  }
}
