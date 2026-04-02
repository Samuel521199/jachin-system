import { NextRequest, NextResponse } from "next/server";
import { getToken } from "next-auth/jwt";
import { resolveAuthSecret } from "@/auth.config";
import { getDb, isDatabaseConfigured } from "@/db";
import { listOrganizationsForUser } from "@/lib/org-membership-db";

export const dynamic = "force-dynamic";

/**
 * GET /api/v1/organizations/list
 *
 * 列出当前登录用户所属的全部组织，并附带会话中 **当前激活** 的 `org_id`（JWT 内）。
 * 用于控制台组织切换器；切换工作区请 `POST /api/v1/organizations/active-org`。
 */
export async function GET(request: NextRequest) {
  const secret = resolveAuthSecret();
  if (!secret) {
    return NextResponse.json(
      { success: false, error: "CONFIG", message: "AUTH_SECRET 未配置（生产环境必填）" },
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
  const activeOrgId =
    typeof token?.orgId === "string" && token.orgId.length > 0
      ? token.orgId
      : null;

  return NextResponse.json({
    success: true,
    data: {
      active_org_id: activeOrgId,
      organizations: rows.map((o) => ({
        org_id: o.orgId,
        name: o.name,
        slug: o.slug,
        role: o.role,
        is_personal_default: o.isPersonalDefault,
      })),
      total: rows.length,
    },
  });
}
