import { NextRequest, NextResponse } from "next/server";
import { eq, or, sql } from "drizzle-orm";
import { getDb, isDatabaseConfigured } from "@/db";
import { organizations } from "@/db/schema";
import { normalizeOrgSlugInput } from "@/lib/org-slug";

export const dynamic = "force-dynamic";

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

/**
 * GET /api/v1/l2-gateway/resolve-org
 *
 * - `organization_id`（可选）：UUID，直接查组织（共享密钥下可信）
 * - `slug`（可选）：先匹配 organizations.slug，再 **lower(trim(name))** 与 slug 相等（忽略大小写与首尾空白）
 */
export async function GET(req: NextRequest) {
  const shared = (process.env.L1_L2_LOGIN_SHARED_SECRET || "").trim();
  if (shared) {
    const sent = (req.headers.get("x-l2-gateway-secret") || "").trim();
    if (sent !== shared) {
      return NextResponse.json(
        { success: false, error: "FORBIDDEN", message: "无效的服务端密钥" },
        { status: 403 }
      );
    }
  } else {
    return NextResponse.json(
      {
        success: false,
        error: "CONFIG",
        message: "未配置 L1_L2_LOGIN_SHARED_SECRET，拒绝服务端解析 slug",
      },
      { status: 503 }
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

  const url = new URL(req.url);
  const orgIdRaw = url.searchParams.get("organization_id")?.trim() ?? "";
  const slugRaw = url.searchParams.get("slug")?.trim() ?? "";

  const db = getDb()!;
  const cols = {
    orgId: organizations.id,
    name: organizations.name,
    slug: organizations.slug,
  };

  if (orgIdRaw && UUID_RE.test(orgIdRaw)) {
    const [row] = await db
      .select(cols)
      .from(organizations)
      .where(eq(organizations.id, orgIdRaw))
      .limit(1);
    if (row) {
      return NextResponse.json({
        success: true,
        data: {
          org_id: row.orgId,
          name: row.name,
          slug: row.slug,
        },
      });
    }
    return NextResponse.json(
      {
        success: false,
        error: "NOT_FOUND",
        message: "未找到该 organization_id",
      },
      { status: 404 }
    );
  }

  const slug = normalizeOrgSlugInput(slugRaw);
  if (!slug) {
    return NextResponse.json(
      {
        success: false,
        error: "INVALID_SLUG",
        message:
          "请提供 slug=（2～64 小写字母数字连字符）或 organization_id=UUID",
      },
      { status: 400 }
    );
  }

  const matches = await db
    .select(cols)
    .from(organizations)
    .where(
      or(
        eq(organizations.slug, slug),
        sql`lower(trim(${organizations.name}::text)) = ${slug}`
      )
    )
    .limit(10);

  if (matches.length === 0) {
    return NextResponse.json(
      {
        success: false,
        error: "NOT_FOUND",
        message:
          "未找到：请确认工作区 slug 或显示名与参数一致，或传 organization_id=UUID",
      },
      { status: 404 }
    );
  }

  const ids = new Set(matches.map((m) => m.orgId));
  if (ids.size > 1) {
    return NextResponse.json(
      {
        success: false,
        error: "AMBIGUOUS",
        message:
          "多个工作区同时匹配，请在 L1 设置唯一 slug，或在 L3 填写 organization_id（UUID）",
      },
      { status: 409 }
    );
  }

  const row = matches[0]!;
  return NextResponse.json({
    success: true,
    data: {
      org_id: row.orgId,
      name: row.name,
      slug: row.slug,
    },
  });
}
