import { NextResponse } from "next/server";
import { eq } from "drizzle-orm";
import { getDb, isDatabaseConfigured } from "@/db";
import { organizationUsers, users } from "@/db/schema";
import { ORG_ROLES_ALL } from "@/lib/org-constants";
import { withOrgRole } from "@/lib/with-org-role";

export const dynamic = "force-dynamic";

/**
 * GET /api/v1/organizations/members
 *
 * 列出**当前会话组织**（JWT `org_id`，不信任 Header）下的全部成员及角色。
 * 任意组织成员（viewer 及以上）可读。
 */
export const GET = withOrgRole(ORG_ROLES_ALL, async (_request, ctx) => {
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
      members: rows.map((r) => ({
        user_id: r.userId,
        role: r.role,
        email: r.email,
        name: r.name,
        joined_at: r.joinedAt?.toISOString() ?? null,
      })),
      total: rows.length,
    },
  });
});
