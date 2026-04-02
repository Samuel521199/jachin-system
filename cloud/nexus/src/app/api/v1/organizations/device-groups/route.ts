import { NextRequest, NextResponse } from "next/server";
import { eq, and, inArray, count } from "drizzle-orm";
import { getDb, isDatabaseConfigured } from "@/db";
import {
  deviceGroups,
  deviceGroupMembers,
  edgeAgents,
} from "@/db/schema";
import { ORG_ROLES_ALL } from "@/lib/org-constants";
import { resolveOrgSession } from "@/lib/with-org-role";

export const dynamic = "force-dynamic";

/**
 * GET /api/v1/organizations/device-groups
 *
 * 当前会话租户下的设备组（车队 / 站点），含组内边缘节点数量、当前用户在组内角色（若有）。
 * 与 `organization_users.role` 互补：后者为租户级，本接口为组级 ACL 覆写层。
 * 已登录但尚未选择工作区：**200 + 空列表**。
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
        groups: [],
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
        message: "当前组织角色无权查看设备组",
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

  const groups = await db
    .select({
      id: deviceGroups.id,
      name: deviceGroups.name,
      description: deviceGroups.description,
      createdAt: deviceGroups.createdAt,
    })
    .from(deviceGroups)
    .where(eq(deviceGroups.orgId, ctx.tenantId))
    .orderBy(deviceGroups.name);

  const groupIds = groups.map((g) => g.id);
  let countMap = new Map<string, number>();
  if (groupIds.length > 0) {
    const countRows = await db
      .select({
        gid: edgeAgents.deviceGroupId,
        n: count(),
      })
      .from(edgeAgents)
      .where(
        and(
          inArray(edgeAgents.deviceGroupId, groupIds),
          eq(edgeAgents.organizationId, ctx.tenantId)
        )
      )
      .groupBy(edgeAgents.deviceGroupId);
    countMap = new Map(
      countRows
        .filter((r) => r.gid != null)
        .map((r) => [r.gid as string, Number(r.n)])
    );
  }

  const myRows = await db
    .select({
      groupId: deviceGroupMembers.groupId,
      role: deviceGroupMembers.role,
    })
    .from(deviceGroupMembers)
    .where(eq(deviceGroupMembers.userId, ctx.userId));
  const myRoleByGroup = Object.fromEntries(
    myRows.map((r) => [r.groupId, r.role])
  );

  return NextResponse.json({
    success: true,
    data: {
      org_id: ctx.tenantId,
      groups: groups.map((g) => ({
        id: g.id,
        name: g.name,
        description: g.description ?? null,
        agent_count: countMap.get(g.id) ?? 0,
        my_group_role: myRoleByGroup[g.id] ?? null,
        created_at: g.createdAt?.toISOString() ?? null,
      })),
      total: groups.length,
    },
  });
}
