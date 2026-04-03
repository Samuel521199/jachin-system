import { NextResponse } from "next/server";
import { getDb, isDatabaseConfigured } from "@/db";
import { ORG_ROLES_CAN_INVITE, ORG_ROLES_INVITABLE, type OrgRole } from "@/lib/org-constants";
import { getOrgMembershipRole } from "@/lib/org-membership-db";
import { signOrgInviteToken } from "@/lib/org-invite";
import { withOrgRole } from "@/lib/with-org-role";

export const dynamic = "force-dynamic";

const MAX_INVITE_TTL_SEC = 7 * 24 * 3600;
const DEFAULT_INVITE_TTL_SEC = 900;

/**
 * POST /api/v1/organizations/members/invite
 *
 * **极简魔法邀请**：仅 Owner / Admin（会话 JWT `orgRole` + DB 双检）可签发短效 HS256 Token。
 * 租户边界 **仅**来自验签会话中的 `org_id`，忽略 `X-Tenant-Id`。
 *
 * Body: `{ "role": "member" | "admin" | "fleet_admin" | "viewer", "expires_in_sec"?: number }`
 */
export const POST = withOrgRole(ORG_ROLES_CAN_INVITE, async (request, ctx) => {
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

  const dbRole = await getOrgMembershipRole(db, ctx.userId, ctx.tenantId);
  if (!dbRole || !ORG_ROLES_CAN_INVITE.includes(dbRole as OrgRole)) {
    return NextResponse.json(
      {
        success: false,
        error: "FORBIDDEN",
        message: "数据库中的成员角色不允许发放邀请（会话可能已过期）",
      },
      { status: 403 }
    );
  }

  let body: { role?: string; expires_in_sec?: number };
  try {
    body = (await request.json()) as { role?: string; expires_in_sec?: number };
  } catch {
    return NextResponse.json(
      { success: false, error: "INVALID_JSON", message: "请求体须为 JSON" },
      { status: 400 }
    );
  }

  const role = typeof body.role === "string" ? body.role.trim() : "";
  if (!(ORG_ROLES_INVITABLE as readonly string[]).includes(role)) {
    return NextResponse.json(
      {
        success: false,
        error: "INVALID_ROLE",
        message: `role 必须是 ${ORG_ROLES_INVITABLE.join(", ")} 之一（不可邀请 owner）`,
      },
      { status: 400 }
    );
  }

  let ttl = DEFAULT_INVITE_TTL_SEC;
  if (typeof body.expires_in_sec === "number" && Number.isFinite(body.expires_in_sec)) {
    ttl = Math.min(
      MAX_INVITE_TTL_SEC,
      Math.max(60, Math.floor(body.expires_in_sec))
    );
  }

  try {
    const token = await signOrgInviteToken({
      orgId: ctx.tenantId,
      invitedRole: role,
      expiresInSec: ttl,
    });
    return NextResponse.json({
      success: true,
      data: {
        token,
        org_id: ctx.tenantId,
        invited_role: role,
        expires_in_sec: ttl,
      },
    });
  } catch (e) {
    console.error("[org invite]", e);
    return NextResponse.json(
      {
        success: false,
        error: "INVITE_SIGN_FAILED",
        message: "无法签发邀请（检查 AUTH_SECRET / NEXUS_ORG_INVITE_SECRET）",
      },
      { status: 500 }
    );
  }
});
