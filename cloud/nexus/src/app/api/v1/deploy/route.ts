import { NextRequest, NextResponse } from "next/server";
import { randomUUID } from "crypto";
import { getSupabase, isSupabaseConfigured } from "@/lib/supabase";

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
    let resourceId = randomUUID();

    if (isSupabaseConfigured()) {
      const sb = getSupabase()!;
      const { data: plugin } = await sb
        .from("plugins_registry")
        .select("id, download_url")
        .eq("plugin_id", plugin_id)
        .eq("status", "approved")
        .maybeSingle();

      if (plugin) {
        downloadUrl = plugin.download_url;
        resourceId = plugin.id;
      }

      const { error } = await sb.from("deploy_commands").insert({
        user_id: user_id ?? DEFAULT_USER_ID,
        layer2_instance_id: instanceId,
        resource_type: "plugin",
        resource_id: resourceId,
        download_url: downloadUrl,
        temp_token: tempToken,
        token_expires_at: tokenExpiresAt.toISOString(),
        status: "pending",
      });

      if (error) {
        console.error("[deploy] Supabase insert error:", error);
        return NextResponse.json(
          { success: false, error: error.message },
          { status: 500 }
        );
      }
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
