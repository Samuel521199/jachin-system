import { NextRequest, NextResponse } from "next/server";
import { getDb, isDatabaseConfigured } from "@/db";
import {
  extractBearerTokenRaw,
  resolveEdgeAgentManifestContext,
} from "@/lib/edge-agent-manifest-auth";
import { listOrganizationsForUser } from "@/lib/org-membership-db";
import { resolveAuthSecret } from "@/auth.config";
import { getToken } from "next-auth/jwt";

export const dynamic = "force-dynamic";

/**
 * GET /api/v1/edge/me/workspaces
 *
 * 供 L2（nexus_config access_token）或浏览器会话拉取工作区列表。
 * - Bearer 为 **edge_agents** 凭证时：按 agent.userId 列出成员工作区（与 me/workspaces 同形）。
 * - 否则：回退为 Auth.js JWT 会话（与 GET /api/v1/me/workspaces 一致），便于 L3 向导用 Cookie。
 */
export async function GET(request: NextRequest) {
  const secret = resolveAuthSecret();
  if (!secret) {
    return NextResponse.json(
      { success: false, error: "CONFIG", message: "AUTH_SECRET 未配置" },
      { status: 500 }
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
  const bearer = extractBearerTokenRaw(request);

  let userId = "";

  if (bearer) {
    const edgeCtx = await resolveEdgeAgentManifestContext(db, bearer);
    if (edgeCtx) {
      if (!edgeCtx.userId) {
        return NextResponse.json(
          {
            success: false,
            error: "FORBIDDEN",
            message: "边缘凭证未绑定用户，无法枚举工作区",
          },
          { status: 403 }
        );
      }
      userId = edgeCtx.userId;
    }
  }

  if (!userId) {
    let token: Awaited<ReturnType<typeof getToken>>;
    try {
      token = await getToken({ req: request, secret });
    } catch {
      return NextResponse.json(
        { success: false, error: "UNAUTHORIZED", message: "无效会话" },
        { status: 401 }
      );
    }
    userId = typeof token?.sub === "string" ? token.sub : "";
    if (!userId) {
      return NextResponse.json(
        {
          success: false,
          error: "UNAUTHORIZED",
          message: "请使用 L2 配对 Bearer 或浏览器登录会话",
        },
        { status: 401 }
      );
    }
  }

  const rows = await listOrganizationsForUser(db, userId);

  return NextResponse.json({
    success: true,
    data: {
      workspaces: rows.map((r) => ({
        id: r.orgId,
        name: r.name,
        slug: r.slug,
        role: r.role,
        is_personal_default: r.isPersonalDefault,
      })),
    },
  });
}
