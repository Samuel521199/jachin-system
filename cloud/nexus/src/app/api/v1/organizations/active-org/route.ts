import { NextRequest, NextResponse } from "next/server";
import { auth, unstable_update } from "@/auth";
import { getDb, isDatabaseConfigured } from "@/db";
import { getOrgMembershipRole } from "@/lib/org-membership-db";
import { isTenantUuidString } from "@/lib/tenant";

export const dynamic = "force-dynamic";

/**
 * POST /api/v1/organizations/active-org
 *
 * **切换当前工作区**：校验用户为该 `org_id` 成员后，通过 Auth.js `unstable_update({ activeOrgId })`
 * 刷新会话 JWT 中的 `orgId` / `orgRole`（仍经服务端 DB 校验，不信任 Header）。
 *
 * 典型流程：魔法加入新组织后调用本接口，再 `GET /api/v1/organizations/list` 确认 `active_org_id`。
 *
 * Body: `{ "org_id": "<organizations.uuid>" }`
 */
export async function POST(request: NextRequest) {
  const session = await auth();
  const userId = session?.user?.id;
  if (!userId) {
    return NextResponse.json(
      { success: false, error: "UNAUTHORIZED", message: "请先登录" },
      { status: 401 }
    );
  }

  let body: { org_id?: string };
  try {
    body = (await request.json()) as { org_id?: string };
  } catch {
    return NextResponse.json(
      { success: false, error: "INVALID_JSON", message: "请求体须为 JSON" },
      { status: 400 }
    );
  }

  const orgId = typeof body.org_id === "string" ? body.org_id.trim() : "";
  if (!orgId || !isTenantUuidString(orgId)) {
    return NextResponse.json(
      { success: false, error: "INVALID_ORG", message: "org_id 须为合法 UUID" },
      { status: 400 }
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
  const role = await getOrgMembershipRole(db, userId, orgId);
  if (!role) {
    return NextResponse.json(
      {
        success: false,
        error: "FORBIDDEN",
        message: "当前用户不是该组织成员",
      },
      { status: 403 }
    );
  }

  try {
    await unstable_update({ activeOrgId: orgId });
  } catch (e) {
    console.error("[active-org] unstable_update", e);
    return NextResponse.json(
      {
        success: false,
        error: "SESSION_UPDATE_FAILED",
        message: "刷新会话失败",
      },
      { status: 500 }
    );
  }

  return NextResponse.json({
    success: true,
    data: {
      org_id: orgId,
      org_role: role,
    },
  });
}
