import { NextRequest, NextResponse } from "next/server";
import { getDb, isDatabaseConfigured } from "@/db";
import { deployCommands } from "@/db/schema";
import { eq, gt, asc, and } from "drizzle-orm";

/**
 * GET /api/v1/deploy/poll?instance_id=xxx
 * Layer 2 Updater Agent 轮询接口
 */
export async function GET(request: NextRequest) {
  const { searchParams } = new URL(request.url);
  const instanceId = searchParams.get("instance_id");

  if (!instanceId) {
    return NextResponse.json(
      { success: false, error: "Missing instance_id" },
      { status: 400 }
    );
  }

  const commands = await getPendingCommands(instanceId);

  return NextResponse.json({
    success: true,
    data: { commands },
  });
}

async function getPendingCommands(instanceId: string): Promise<unknown[]> {
  if (!isDatabaseConfigured()) return [];

  const db = getDb()!;

  const rows = await db
    .select({
      id: deployCommands.id,
      downloadUrl: deployCommands.downloadUrl,
      tempToken: deployCommands.tempToken,
      pluginId: deployCommands.pluginId,
      resourceType: deployCommands.resourceType,
    })
    .from(deployCommands)
    .where(
      and(
        eq(deployCommands.layer2InstanceId, instanceId),
        eq(deployCommands.status, "pending"),
        gt(deployCommands.tokenExpiresAt, new Date())
      )
    )
    .orderBy(asc(deployCommands.createdAt));

  if (rows.length === 0) return [];

  const commands = rows.map((r) => ({
    temp_token: r.tempToken,
    download_url: r.downloadUrl,
    plugin_id: r.pluginId ?? "unknown",
    resource_type: r.resourceType ?? "plugin",
  }));

  for (const r of rows) {
    await db
      .update(deployCommands)
      .set({ status: "delivered" })
      .where(eq(deployCommands.id, r.id));
  }

  return commands;
}
