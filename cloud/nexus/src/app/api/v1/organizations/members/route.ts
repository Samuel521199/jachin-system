import { NextRequest, NextResponse } from "next/server";
import { eq } from "drizzle-orm";
import { getDb, isDatabaseConfigured } from "@/db";
import { organizationUsers, users } from "@/db/schema";
import { ORG_ROLES_ALL } from "@/lib/org-constants";
import { resolveOrgSession } from "@/lib/with-org-role";

export const dynamic = "force-dynamic";

/**
 * GET /api/v1/organizations/members
 *
 * 列出**当前会话组织**（JWT `org_id`，不信任 Header）下的全部成员及角色。
 * 任意组织成员（viewer 及以上）可读。
 * 已登录但尚未选择工作区：**200 + 空列表**（避免控制台首屏 403）。
 */
export async function GET(request: NextRequest) {
  const r = await resolveOrgSession(request, ORG_ROLES_ALL);
  if (r.type === "no_config") {
    return NextResponse.json(
      {
        success: false,
        error: "CONFIG",
        message: "AUTH_SECRET 未配置（生产环境必填）",
      },
      { status: 500 }
    );
  }
  if (r.type === "unauthorized") {
    return NextResponse.json(
      { success: false, error: "UNAUTHORIZED", message: "需要登录" },
      { status: 401 }
    );
  }
  if (r.type === "no_org") {
    return NextResponse.json({
      success: true,
      data: {
        org_id: null,
        members: [],
        total: 0,
      },
      meta: { no_active_org: true },
    });
  }
  if (r.type === "forbidden_role") {
    return NextResponse.json(
      {
        success: false,
        error: "FORBIDDEN",
        message: "当前组织角色无权查看成员列表",
      },
      { status: 403 }
    );
  }

  const ctx = r.ctx;
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
  const rows = await db
    .select({
      userId: organizationUsers.userId,
      role: organizationUsers.role,
      email: users.email,
      name: users.name,
      joinedAt: organizationUsers.createdAt,
    })
    .from(organizationUsers)
    .innerJoin(users, eq(users.id, organizationUsers.userId))
    .where(eq(organizationUsers.orgId, ctx.tenantId));

  return NextResponse.json({
    success: true,
    data: {
      org_id: ctx.tenantId,
      members: rows.map((row) => ({
        user_id: row.userId,
        role: row.role,
        email: row.email,
        name: row.name,
        joined_at: row.joinedAt?.toISOString() ?? null,
      })),
      total: rows.length,
    },
  });
}
