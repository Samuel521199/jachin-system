import { NextRequest, NextResponse } from "next/server";
import { getToken } from "next-auth/jwt";
import { resolveAuthSecret } from "@/auth.config";
import { getDb, isDatabaseConfigured } from "@/db";
import { listOrganizationsForUser } from "@/lib/org-membership-db";

export const dynamic = "force-dynamic";

/**
 * GET /api/v1/me/workspaces
 *
 * 供 L3 设备端配置向导拉取「发往 L2 的 organization_id」下拉列表（非 L1↔L3 配对）。
 * 返回当前登录用户已加入的工作区：id、名称、角色。
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
  const rows = await listOrganizationsForUser(db, userId);

  return NextResponse.json({
    success: true,
    data: {
      workspaces: rows.map((r) => ({
        id: r.orgId,
        name: r.name,
        slug: r.slug,
        role: r.role,
        is_personal_default: r.isPersonalDefault,
      })),
    },
  });
}
