import { NextRequest, NextResponse } from "next/server";
import { getDb, isDatabaseConfigured } from "@/db";
import {
  extractBearerTokenRaw,
  resolveEdgeAgentManifestContext,
} from "@/lib/edge-agent-manifest-auth";
import { getOrganizationBySlugForUser } from "@/lib/org-membership-db";
import { normalizeOrgSlugInput } from "@/lib/org-slug";

export const dynamic = "force-dynamic";

/**
 * GET /api/v1/edge/resolve-org?slug=
 *
 * L2 在 L3 auth/sync 中用 edge Bearer 将 **slug → organizations.id**（须为成员）。
 */
export async function GET(request: NextRequest) {
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

  const bearer = extractBearerTokenRaw(request);
  if (!bearer) {
    return NextResponse.json(
      { success: false, error: "UNAUTHORIZED", message: "缺少 Authorization Bearer" },
      { status: 401 }
    );
  }

  const slugRaw = request.nextUrl.searchParams.get("slug")?.trim() ?? "";
  const slug = normalizeOrgSlugInput(slugRaw);
  if (!slug) {
    return NextResponse.json(
      { success: false, error: "INVALID_SLUG", message: "slug 参数无效" },
      { status: 400 }
    );
  }

  const db = getDb()!;
  const edgeCtx = await resolveEdgeAgentManifestContext(db, bearer);
  if (!edgeCtx) {
    return NextResponse.json(
      { success: false, error: "UNAUTHORIZED", message: "无效边缘凭证" },
      { status: 401 }
    );
  }
  if (!edgeCtx.userId) {
    return NextResponse.json(
      {
        success: false,
        error: "FORBIDDEN",
        message: "边缘凭证未绑定用户，无法解析 slug",
      },
      { status: 403 }
    );
  }

  const org = await getOrganizationBySlugForUser(db, edgeCtx.userId, slug);
  if (!org) {
    return NextResponse.json(
      {
        success: false,
        error: "NOT_FOUND",
        message: "未找到该 slug 或当前用户非其成员",
      },
      { status: 404 }
    );
  }

  return NextResponse.json({
    success: true,
    data: {
      org_id: org.orgId,
      name: org.name,
      slug: org.slug,
    },
  });
}
