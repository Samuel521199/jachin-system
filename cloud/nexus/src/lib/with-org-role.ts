/**
 * RBAC 高阶包装：从 Auth.js 会话 JWT（getToken 验签）读取 `sub` / `orgId` / `orgRole`，
 * **不信任** `X-Tenant-Id` 等客户端头作为租户边界。
 */
import { NextRequest, NextResponse } from "next/server";
import { getToken } from "next-auth/jwt";

export type OrgAuthContext = {
  /** users.id */
  userId: string;
  /** organizations.id（tenant_id） */
  tenantId: string;
  /** organization_users.role */
  orgRole: string;
};

/**
 * 包装 App Router handler：仅当会话中存在合法 `orgId` 且 `orgRole` 落在 `allowedRoles` 内时执行。
 */
export function withOrgRole(
  allowedRoles: readonly string[],
  handler: (req: NextRequest, ctx: OrgAuthContext) => Promise<Response>
): (req: NextRequest) => Promise<Response> {
  return async (req: NextRequest) => {
    const secret = process.env.AUTH_SECRET;
    if (!secret) {
      return NextResponse.json(
        {
          success: false,
          error: "CONFIG",
          message: "AUTH_SECRET is not configured",
        },
        { status: 500 }
      );
    }
    let token: Awaited<ReturnType<typeof getToken>>;
    try {
      token = await getToken({ req, secret });
    } catch {
      return NextResponse.json(
        {
          success: false,
          error: "UNAUTHORIZED",
          message: "Invalid session",
        },
        { status: 401 }
      );
    }
    const userId = typeof token?.sub === "string" ? token.sub : "";
    const tenantId = typeof token?.orgId === "string" ? token.orgId : "";
    const orgRole = typeof token?.orgRole === "string" ? token.orgRole : "";
    if (!userId || !tenantId) {
      return NextResponse.json(
        {
          success: false,
          error: "UNAUTHORIZED",
          message: "需要登录，且会话须包含组织上下文（org_id）",
        },
        { status: 401 }
      );
    }
    if (!allowedRoles.includes(orgRole)) {
      return NextResponse.json(
        {
          success: false,
          error: "FORBIDDEN",
          message: "当前组织角色无权执行此操作",
        },
        { status: 403 }
      );
    }
    return handler(req, { userId, tenantId, orgRole });
  };
}
