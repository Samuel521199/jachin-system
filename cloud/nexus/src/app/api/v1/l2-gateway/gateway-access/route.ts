import { NextRequest, NextResponse } from "next/server";
import { and, eq, or } from "drizzle-orm";
import { getDb, isDatabaseConfigured } from "@/db";
import { edgeAgents } from "@/db/schema";
import { extractBearerTokenRaw } from "@/lib/edge-agent-manifest-auth";
import { isTenantUuidString } from "@/lib/tenant";
import { userCanManageL2Gateway } from "@/lib/l1-workspace-context";

export const dynamic = "force-dynamic";

/**
 * GET /api/v1/l2-gateway/gateway-access
 * Authorization: Bearer <edge_agents.access_token 或 instance id>
 *
 * 供 L2「快速登录」校验：当前配对凭证对应的 L1 用户是否具备访问 L2 网关的身份
 *（工作区 owner / admin）。普通成员返回 allowed: false。
 */
export async function GET(req: NextRequest) {
  const bearer = extractBearerTokenRaw(req);
  if (!bearer) {
    return NextResponse.json(
      {
        success: false,
        error: "UNAUTHORIZED",
        message: "缺少 Authorization: Bearer",
        allowed: false,
      },
      { status: 401 }
    );
  }

  if (!isDatabaseConfigured()) {
    return NextResponse.json({
      success: true,
      allowed: true,
      message: "演示模式（无数据库）默认允许",
    });
  }

  const db = getDb()!;
  const tokenOrId = isTenantUuidString(bearer)
    ? or(eq(edgeAgents.authToken, bearer), eq(edgeAgents.id, bearer))
    : eq(edgeAgents.authToken, bearer);

  const [agent] = await db
    .select({
      userId: edgeAgents.userId,
      organizationId: edgeAgents.organizationId,
    })
    .from(edgeAgents)
    .where(and(eq(edgeAgents.status, "active"), tokenOrId))
    .limit(1);

  if (!agent?.userId) {
    return NextResponse.json({
      success: true,
      allowed: false,
      reason: "INVALID_TOKEN",
      message: "无效或未激活的边缘凭证",
    });
  }

  const orgId = agent.organizationId?.trim() ?? "";
  if (!orgId) {
    return NextResponse.json({
      success: true,
      allowed: false,
      reason: "NO_ORGAN",
      message: "边缘实例未绑定工作区，无法判断网关权限",
    });
  }

  const allowed = await userCanManageL2Gateway(db, agent.userId, orgId);

  return NextResponse.json({
    success: true,
    allowed,
    reason: allowed ? undefined : "NOT_OWNER_OR_ADMIN",
    message: allowed
      ? "ok"
      : "权限不足：仅工作区所有者或管理员可访问 L2 网关，请使用具备管理权限的账号重新配对",
  });
}
