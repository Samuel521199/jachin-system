import { NextRequest, NextResponse } from "next/server";

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

  // TODO: 查询 deploy_commands 表，status=pending, layer2_instance_id=instanceId
  // 返回后标记为 delivered
  const commands = await getPendingCommands(instanceId);

  return NextResponse.json({
    success: true,
    data: { commands },
  });
}

async function getPendingCommands(instanceId: string): Promise<unknown[]> {
  void instanceId; // TODO: 查询 deploy_commands 表，status=pending, layer2_instance_id=instanceId
  return [];
}
