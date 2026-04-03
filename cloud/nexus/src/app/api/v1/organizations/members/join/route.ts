import { NextRequest, NextResponse } from "next/server";
import { getToken } from "next-auth/jwt";
import { resolveAuthSecret } from "@/auth.config";
import { and, eq } from "drizzle-orm";
import { getDb, isDatabaseConfigured } from "@/db";
import { organizationUsers } from "@/db/schema";
import { verifyOrgInviteToken } from "@/lib/org-invite";
import { ORG_ROLES_INVITABLE, type OrgRole } from "@/lib/org-constants";
import { isTenantUuidString } from "@/lib/tenant";

export const dynamic = "force-dynamic";

/**
 * POST /api/v1/organizations/members/join
 *
 * **一键加入**：当前登录用户（Auth.js 会话 `sub`）持有效魔法邀请 Token，写入 `organization_users`。
 * 若已是该组织成员，则返回成功且不覆盖现有角色（幂等）。
 *
 * Body: `{ "token": "<invite_jwt>" }`
 */
export async function POST(request: NextRequest) {
  const secret = resolveAuthSecret();
  if (!secret) {
    return NextResponse.json(
      { success: false, error: "CONFIG", message: "AUTH_SECRET 未配置（生产环境必填）" },
      { status: 500 }
    );
  }

  let session;
  try {
    session = await getToken({ req: request, secret });
  } catch {
    return NextResponse.json(
      { success: false, error: "UNAUTHORIZED", message: "无效会话" },
      { status: 401 }
    );
  }
  const userId = typeof session?.sub === "string" ? session.sub : "";
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

  let body: { token?: string };
  try {
    body = (await request.json()) as { token?: string };
  } catch {
    return NextResponse.json(
      { success: false, error: "INVALID_JSON", message: "请求体须为 JSON" },
      { status: 400 }
    );
  }

  const rawToken = typeof body.token === "string" ? body.token.trim() : "";
  if (!rawToken) {
    return NextResponse.json(
      { success: false, error: "MISSING_TOKEN", message: "缺少 token" },
      { status: 400 }
    );
  }

  const verified = await verifyOrgInviteToken(rawToken);
  if ("error" in verified) {
    return NextResponse.json(
      { success: false, error: verified.error, message: verified.message },
      { status: 400 }
    );
  }

  const { org_id, invited_role } = verified.payload;
  if (!(ORG_ROLES_INVITABLE as readonly string[]).includes(invited_role)) {
    return NextResponse.json(
      {
        success: false,
        error: "INVALID_ROLE",
        message: "邀请中的角色非法或不可通过邀请落地",
      },
      { status: 400 }
    );
  }
  if (!isTenantUuidString(org_id)) {
    return NextResponse.json(
      { success: false, error: "INVALID_ORG", message: "邀请中的 org_id 无效" },
      { status: 400 }
    );
  }

  const db = getDb()!;

  const [existing] = await db
    .select({
      role: organizationUsers.role,
    })
    .from(organizationUsers)
    .where(
      and(
        eq(organizationUsers.orgId, org_id),
        eq(organizationUsers.userId, userId)
      )
    )
    .limit(1);

  if (existing) {
    return NextResponse.json({
      success: true,
      data: {
        org_id,
        user_id: userId,
        role: existing.role,
        already_member: true,
      },
    });
  }

  await db.insert(organizationUsers).values({
    orgId: org_id,
    userId: userId,
    role: invited_role as OrgRole,
  });

  return NextResponse.json({
    success: true,
    data: {
      org_id,
      user_id: userId,
      role: invited_role,
      already_member: false,
      /** 加入后若需立刻以该组织为租户调用业务 API，请 `POST /api/v1/organizations/active-org` */
      switch_context_hint: true,
    },
  });
}
