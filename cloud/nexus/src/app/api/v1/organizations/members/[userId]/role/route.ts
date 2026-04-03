import { NextRequest, NextResponse } from "next/server";
import { and, eq } from "drizzle-orm";
import { getDb, isDatabaseConfigured } from "@/db";
import { organizationUsers } from "@/db/schema";
import {
  ORG_ROLES_CAN_MANAGE_MEMBERS,
  ORG_ROLES_INVITABLE,
  type OrgRole,
} from "@/lib/org-constants";
import { getOrgMembershipRole } from "@/lib/org-membership-db";
import { withOrgRole } from "@/lib/with-org-role";

export const dynamic = "force-dynamic";

const ASSIGNABLE_ROLES = ORG_ROLES_INVITABLE;

/**
 * PUT /api/v1/organizations/members/:userId/role
 *
 * 修改成员角色（仅 Owner / Admin）。`tenant_id` **仅**来自会话 JWT，不信任 Header。
 * 禁止将成员设为 `owner`（须走所有权转让）；Admin **不可**修改 Owner 账号。
 *
 * Body: `{ "role": "admin" | "member" | "fleet_admin" | "viewer" }`
 */
export async function PUT(
  request: NextRequest,
  context: { params: { userId: string } }
) {
  const targetUserId = context.params.userId?.trim();
  if (!targetUserId) {
    return NextResponse.json(
      { success: false, error: "BAD_REQUEST", message: "缺少 userId" },
      { status: 400 }
    );
  }

  const run = withOrgRole(ORG_ROLES_CAN_MANAGE_MEMBERS, async (req, ctx) => {
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

    const actorDbRole = await getOrgMembershipRole(db, ctx.userId, ctx.tenantId);
    if (
      !actorDbRole ||
      !ORG_ROLES_CAN_MANAGE_MEMBERS.includes(actorDbRole as OrgRole)
    ) {
      return NextResponse.json(
        { success: false, error: "FORBIDDEN", message: "无权修改成员" },
        { status: 403 }
      );
    }

    const targetDbRole = await getOrgMembershipRole(
      db,
      targetUserId,
      ctx.tenantId
    );
    if (!targetDbRole) {
      return NextResponse.json(
        { success: false, error: "NOT_FOUND", message: "该用户不在此组织中" },
        { status: 404 }
      );
    }

    if (targetDbRole === "owner" && actorDbRole === "admin") {
      return NextResponse.json(
        {
          success: false,
          error: "FORBIDDEN",
          message: "Admin 不能修改 Owner 的角色",
        },
        { status: 403 }
      );
    }

    let body: { role?: string };
    try {
      body = (await req.json()) as { role?: string };
    } catch {
      return NextResponse.json(
        { success: false, error: "INVALID_JSON", message: "请求体须为 JSON" },
        { status: 400 }
      );
    }

    const newRole =
      typeof body.role === "string" ? body.role.trim() : "";
    if (!(ASSIGNABLE_ROLES as readonly string[]).includes(newRole)) {
      return NextResponse.json(
        {
          success: false,
          error: "INVALID_ROLE",
          message: `role 必须是 ${ASSIGNABLE_ROLES.join(", ")} 之一（不可设为 owner）`,
        },
        { status: 400 }
      );
    }

    if (targetDbRole === "owner") {
      return NextResponse.json(
        {
          success: false,
          error: "INVALID_TARGET",
          message: "不能通过本接口修改 Owner 角色（需转让所有权）",
        },
        { status: 400 }
      );
    }

    await db
      .update(organizationUsers)
      .set({
        role: newRole as (typeof organizationUsers.$inferInsert)["role"],
      })
      .where(
        and(
          eq(organizationUsers.orgId, ctx.tenantId),
          eq(organizationUsers.userId, targetUserId)
        )
      );

    return NextResponse.json({
      success: true,
      data: {
        org_id: ctx.tenantId,
        user_id: targetUserId,
        role: newRole,
      },
    });
  });

  return run(request);
}
