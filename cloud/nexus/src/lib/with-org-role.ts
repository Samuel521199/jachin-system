/**
 * RBAC 高阶包装：从 Auth.js 会话 JWT（getToken 验签）读取 `sub` / `orgId` / `orgRole`，
 * **不信任** `X-Tenant-Id` 等客户端头作为租户边界。
 */
import { NextRequest, NextResponse } from "next/server";
import { getToken } from "next-auth/jwt";
import { resolveAuthSecret } from "@/auth.config";
import { jsonOrgRequiredResponse } from "@/lib/org-session-guard";

export type OrgAuthContext = {
  /** users.id */
  userId: string;
  /** organizations.id（tenant_id） */
  tenantId: string;
  /** organization_users.role */
  orgRole: string;
};

/** 从请求解析会话组织上下文（供 withOrgRole 与「无工作区时返回空列表」类 GET 共用） */
export type ResolvedOrgSession =
  | { type: "ok"; ctx: OrgAuthContext }
  | { type: "no_config" }
  | { type: "unauthorized" }
  | { type: "no_org" }
  | { type: "forbidden_role" };

export async function resolveOrgSession(
  req: NextRequest,
  allowedRoles: readonly string[]
): Promise<ResolvedOrgSession> {
  const secret = resolveAuthSecret();
  if (!secret) {
    return { type: "no_config" };
  }
  let token: Awaited<ReturnType<typeof getToken>>;
  try {
    token = await getToken({ req, secret });
  } catch {
    return { type: "unauthorized" };
  }
  const userId = typeof token?.sub === "string" ? token.sub : "";
  const tenantId =
    typeof token?.orgId === "string" ? token.orgId.trim() : "";
  const orgRole = typeof token?.orgRole === "string" ? token.orgRole : "";
  if (!userId) {
    return { type: "unauthorized" };
  }
  if (!tenantId) {
    return { type: "no_org" };
  }
  if (!allowedRoles.includes(orgRole)) {
    return { type: "forbidden_role" };
  }
  return { type: "ok", ctx: { userId, tenantId, orgRole } };
}

function jsonConfigError(): Response {
  return NextResponse.json(
    {
      success: false,
      error: "CONFIG",
      message: "AUTH_SECRET 未配置（生产环境必填）",
    },
    { status: 500 }
  );
}

function jsonUnauthorized(message = "需要登录"): Response {
  return NextResponse.json(
    { success: false, error: "UNAUTHORIZED", message },
    { status: 401 }
  );
}

function jsonForbiddenRole(): Response {
  return NextResponse.json(
    {
      success: false,
      error: "FORBIDDEN",
      message: "当前组织角色无权执行此操作",
    },
    { status: 403 }
  );
}

/**
 * 包装 App Router handler：仅当会话中存在合法 `orgId` 且 `orgRole` 落在 `allowedRoles` 内时执行。
 * 已登录但无当前工作区：**403 + ORG_REQUIRED**（写操作 / 舰队等须显式选组织）。
 */
export function withOrgRole(
  allowedRoles: readonly string[],
  handler: (req: NextRequest, ctx: OrgAuthContext) => Promise<Response>
): (req: NextRequest) => Promise<Response> {
  return async (req: NextRequest) => {
    const r = await resolveOrgSession(req, allowedRoles);
    switch (r.type) {
      case "no_config":
        return jsonConfigError();
      case "unauthorized":
        return jsonUnauthorized("需要登录或会话无效");
      case "no_org":
        return jsonOrgRequiredResponse();
      case "forbidden_role":
        return jsonForbiddenRole();
      case "ok":
        return handler(req, r.ctx);
    }
  };
}
