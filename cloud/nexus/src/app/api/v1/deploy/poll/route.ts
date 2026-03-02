import { NextRequest, NextResponse } from "next/server";
import { getSupabase, isSupabaseConfigured } from "@/lib/supabase";

/**
 * GET /api/v1/deploy/poll?instance_id=xxx
 * Layer 2 Updater Agent 轮询接口
 * 返回该实例的待执行部署指令
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
  if (!isSupabaseConfigured()) {
    return [];
  }

  const sb = getSupabase()!;

  const { data: rows, error } = await sb
    .from("deploy_commands")
    .select("id, download_url, temp_token, plugin_id, resource_type")
    .eq("layer2_instance_id", instanceId)
    .eq("status", "pending")
    .gt("token_expires_at", new Date().toISOString())
    .order("created_at", { ascending: true });

  if (error) {
    console.error("[deploy/poll] query error:", error);
    return [];
  }

  if (!rows?.length) return [];

  const commands = rows.map((r) => ({
    temp_token: r.temp_token,
    download_url: r.download_url,
    plugin_id: r.plugin_id ?? "unknown",
    resource_type: r.resource_type ?? "plugin",
  }));

  for (const r of rows) {
    await sb
      .from("deploy_commands")
      .update({ status: "delivered" })
      .eq("id", r.id);
  }

  return commands;
}
