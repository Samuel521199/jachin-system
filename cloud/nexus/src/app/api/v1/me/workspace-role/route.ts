import { NextRequest, NextResponse } from "next/server";
import { getToken } from "next-auth/jwt";
import { resolveAuthSecret } from "@/auth.config";
import { getDb, isDatabaseConfigured } from "@/db";
import { userCanManageL2Gateway } from "@/lib/l1-workspace-context";

export const dynamic = "force-dynamic";

/**
 * GET /api/v1/me/workspace-role?org_id=<uuid>
 *
 * 查询当前用户对指定工作区是否具备 L2 边缘网关管理身份（owner/admin）。
 */
export async function GET(request: NextRequest) {
  const secret = resolveAuthSecret();
  if (!secret) {
    return NextResponse.json(
      { success: false, error: "CONFIG", message: "AUTH_SECRET 未配置" },
      { status: 500 }
    );
  }

  let token: Awaited<ReturnType<typeof getToken>>;
  try {
    token = await getToken({ req: request, secret });
  } catch {
    return NextResponse.json(
      { success: false, error: "UNAUTHORIZED", message: "无效会话" },
      { status: 401 }
    );
  }

  const userId = typeof token?.sub === "string" ? token.sub : "";
  if (!userId) {
    return NextResponse.json(
      { success: false, error: "UNAUTHORIZED", message: "请先登录" },
      { status: 401 }
    );
  }

  const orgId = new URL(request.url).searchParams.get("org_id")?.trim() ?? "";
  if (!orgId) {
    return NextResponse.json(
      {
        success: false,
        error: "BAD_REQUEST",
        message: "缺少查询参数 org_id",
      },
      { status: 400 }
    );
  }

  if (!isDatabaseConfigured()) {
    return NextResponse.json(
      {
        success: false,
        error: "DATABASE_UNAVAILABLE",
        message: "未配置 DATABASE_URL",
      },
      { status: 503 }
    );
  }

  const db = getDb()!;
  const can = await userCanManageL2Gateway(db, userId, orgId);

  return NextResponse.json({
    success: true,
    data: {
      org_id: orgId,
      can_manage_l2_gateway: can,
    },
  });
}
