import { NextRequest, NextResponse } from "next/server";
import { getToken } from "next-auth/jwt";
import { resolveAuthSecret } from "@/auth.config";
import { getDb, isDatabaseConfigured } from "@/db";
import { organizations, organizationUsers } from "@/db/schema";
import { eq } from "drizzle-orm";
import { normalizeOrgSlugInput } from "@/lib/org-slug";

export const dynamic = "force-dynamic";

const NAME_MIN = 1;
const NAME_MAX = 128;

/**
 * POST /api/v1/organizations/create
 *
 * 登录用户创建**新的团队工作区**：`is_personal_default = false`，创建者为 `owner`。
 * 创建后可 `POST .../active-org` 切换会话上下文。
 *
 * Body: `{ "name": "显示名称", "slug": "可选短码" }`
 */
export async function POST(request: NextRequest) {
  const secret = resolveAuthSecret();
  if (!secret) {
    return NextResponse.json(
      { success: false, error: "CONFIG", message: "AUTH_SECRET 未配置（生产环境必填）" },
      { status: 500 }
    );
  }

  let jwt: Awaited<ReturnType<typeof getToken>>;
  try {
    jwt = await getToken({ req: request, secret });
  } catch {
    return NextResponse.json(
      { success: false, error: "UNAUTHORIZED", message: "无效会话" },
      { status: 401 }
    );
  }

  const userId = typeof jwt?.sub === "string" ? jwt.sub : "";
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

  let body: { name?: string; slug?: string };
  try {
    body = (await request.json()) as { name?: string; slug?: string };
  } catch {
    return NextResponse.json(
      { success: false, error: "INVALID_JSON", message: "请求体须为 JSON" },
      { status: 400 }
    );
  }

  const name =
    typeof body.name === "string" ? body.name.trim().replace(/\s+/g, " ") : "";
  if (name.length < NAME_MIN || name.length > NAME_MAX) {
    return NextResponse.json(
      {
        success: false,
        error: "INVALID_NAME",
        message: `工作区名称长度须在 ${NAME_MIN}～${NAME_MAX} 字符之间`,
      },
      { status: 400 }
    );
  }

  const slugNorm =
    body.slug !== undefined && String(body.slug).trim()
      ? normalizeOrgSlugInput(body.slug)
      : null;
  if (body.slug !== undefined && String(body.slug).trim() && !slugNorm) {
    return NextResponse.json(
      {
        success: false,
        error: "INVALID_SLUG",
        message:
          "slug 须为 2～64 字符，小写字母、数字与连字符，且不能以连字符开头或结尾",
      },
      { status: 400 }
    );
  }

  const db = getDb()!;

  if (slugNorm) {
    const [taken] = await db
      .select({ id: organizations.id })
      .from(organizations)
      .where(eq(organizations.slug, slugNorm))
      .limit(1);
    if (taken) {
      return NextResponse.json(
        {
          success: false,
          error: "SLUG_TAKEN",
          message: "该短码已被占用",
        },
        { status: 409 }
      );
    }
  }

  try {
    const created = await db.transaction(async (tx) => {
      const [org] = await tx
        .insert(organizations)
        .values({
          name,
          slug: slugNorm,
          billingPlan: "free",
          isPersonalDefault: false,
        })
        .returning({
          id: organizations.id,
          name: organizations.name,
          slug: organizations.slug,
        });
      if (!org) throw new Error("insert organizations returned no row");

      await tx.insert(organizationUsers).values({
        orgId: org.id,
        userId,
        role: "owner",
      });

      return org;
    });

    return NextResponse.json({
      success: true,
      data: {
        org_id: created.id,
        name: created.name,
        slug: created.slug,
      },
    });
  } catch (e) {
    console.error("[organizations/create]", e);
    return NextResponse.json(
      {
        success: false,
        error: "CREATE_FAILED",
        message: "创建工作区失败",
      },
      { status: 500 }
    );
  }
}
