import { NextRequest, NextResponse } from "next/server";
import { randomUUID } from "crypto";
import { getDb, isDatabaseConfigured } from "@/db";
import { pluginsRegistry, deployCommands } from "@/db/schema";
import { eq, and } from "drizzle-orm";

const DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000001";

export interface DeployRequestBody {
  plugin_id: string;
  target_instance_id?: string;
  user_id?: string;
}

/**
 * POST /api/v1/deploy
 * 部署指令 API - 用户点击「Deploy to Layer 2」时调用
 */
export async function POST(request: NextRequest) {
  try {
    const body: DeployRequestBody = await request.json();
    const { plugin_id, target_instance_id, user_id } = body;

    if (!plugin_id) {
      return NextResponse.json(
        { success: false, error: "Missing plugin_id" },
        { status: 400 }
      );
    }

    const instanceId = target_instance_id || "dev-layer2-instance-001";
    const tempToken = randomUUID();
    const tokenExpiresAt = new Date(Date.now() + 15 * 60 * 1000);

    let downloadUrl = `ipfs://QmMock${plugin_id.replace(/\./g, "-")}`;
    let resourceId = randomUUID() as `${string}-${string}-${string}-${string}-${string}`;

    if (isDatabaseConfigured()) {
      const db = getDb()!;
      const [plugin] = await db
        .select({ id: pluginsRegistry.id, downloadUrl: pluginsRegistry.downloadUrl })
        .from(pluginsRegistry)
        .where(
          and(
            eq(pluginsRegistry.pluginId, plugin_id),
            eq(pluginsRegistry.status, "approved")
          )
        )
        .limit(1);

      if (plugin) {
        downloadUrl = plugin.downloadUrl;
        resourceId = plugin.id as `${string}-${string}-${string}-${string}-${string}`;
      }

      await db.insert(deployCommands).values({
        userId: user_id ?? DEFAULT_USER_ID,
        layer2InstanceId: instanceId,
        resourceType: "plugin",
        resourceId,
        pluginId: plugin_id,
        downloadUrl,
        tempToken,
        tokenExpiresAt,
        status: "pending",
      });
    }

    return NextResponse.json({
      success: true,
      data: {
        temp_token: tempToken,
        token_expires_at: tokenExpiresAt.toISOString(),
        download_url: downloadUrl,
        plugin_id,
        target_instance_id: instanceId,
        message:
          "部署指令已生成。请确保 Layer 2 已启动 Updater Agent 并配置了本实例 ID。",
      },
    });
  } catch (e) {
    console.error("[deploy] Unexpected error:", e);
    return NextResponse.json(
      { success: false, error: "Internal server error" },
      { status: 500 }
    );
  }
}
