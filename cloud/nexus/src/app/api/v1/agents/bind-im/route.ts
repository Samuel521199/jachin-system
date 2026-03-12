import { NextRequest, NextResponse } from "next/server";
import { getDb, isDatabaseConfigured } from "@/db";
import { edgeAgents } from "@/db/schema";
import { eq, and } from "drizzle-orm";

const DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000001";

/**
 * POST /api/v1/agents/bind-im
 * 绑定 IM（Telegram / 飞书）到边缘智能体
 *
 * Body: { agent_id: string, im_binding_id: string, im_platform?: 'telegram' | 'lark' }
 */
export async function POST(req: NextRequest) {
  try {
    const body = await req.json().catch(() => ({}));
    const { agent_id, im_binding_id, im_platform } = body;

    if (!agent_id || !im_binding_id || typeof im_binding_id !== "string") {
      return NextResponse.json(
        { success: false, error: "agent_id and im_binding_id required" },
        { status: 400 }
      );
    }

    const platform = (im_platform || "telegram") as string;
    if (!["telegram", "lark"].includes(platform)) {
      return NextResponse.json(
        { success: false, error: "im_platform must be telegram or lark" },
        { status: 400 }
      );
    }

    if (!isDatabaseConfigured()) {
      return NextResponse.json(
        { success: false, error: "数据库未配置" },
        { status: 503 }
      );
    }

    const db = getDb()!;
    const userId = body.user_id ?? DEFAULT_USER_ID;

    const result = await db
      .update(edgeAgents)
      .set({
        imBindingId: String(im_binding_id).trim(),
        imPlatform: platform,
        updatedAt: new Date(),
      })
      .where(and(eq(edgeAgents.id, agent_id), eq(edgeAgents.userId, userId)))
      .returning({ id: edgeAgents.id });

    if (result.length === 0) {
      return NextResponse.json(
        { success: false, error: "智能体不存在或无权操作" },
        { status: 404 }
      );
    }

    return NextResponse.json({ success: true });
  } catch (e) {
    console.error("[agents/bind-im] Error:", e);
    return NextResponse.json(
      { error: "绑定失败" },
      { status: 500 }
    );
  }
}
