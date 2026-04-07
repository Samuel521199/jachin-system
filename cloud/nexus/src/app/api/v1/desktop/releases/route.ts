import { NextRequest, NextResponse } from "next/server";
import { desc } from "drizzle-orm";
import { getToken } from "next-auth/jwt";
import { resolveAuthSecret } from "@/auth.config";
import { getDb, isDatabaseConfigured } from "@/db";
import { desktopAppReleases } from "@/db/schema";

export const dynamic = "force-dynamic";

/**
 * GET /api/v1/desktop/releases
 * 已登录用户：列出历史版本元数据（不含直链；下载走 presign）。
 */
export async function GET(request: NextRequest) {
  const secret = resolveAuthSecret();
  if (!secret) {
    return NextResponse.json(
      { success: false, error: "CONFIG", message: "AUTH_SECRET 未配置" },
      { status: 500 }
    );
  }

  let token: Awaited<ReturnType<typeof getToken>>;
  try {
    token = await getToken({ req: request, secret });
  } catch {
    return NextResponse.json(
      { success: false, error: "UNAUTHORIZED", message: "无效会话" },
      { status: 401 }
    );
  }

  const userId = typeof token?.sub === "string" ? token.sub : "";
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

  const db = getDb()!;
  const rows = await db
    .select({
      version: desktopAppReleases.version,
      notes: desktopAppReleases.notes,
      pubDate: desktopAppReleases.pubDate,
      artifacts: desktopAppReleases.artifacts,
      createdAt: desktopAppReleases.createdAt,
    })
    .from(desktopAppReleases)
    .orderBy(desc(desktopAppReleases.pubDate));

  const releases = rows.map((r) => ({
    version: r.version,
    notes: r.notes ?? "",
    pub_date: r.pubDate.toISOString(),
    platforms: Object.keys(r.artifacts ?? {}),
    created_at: r.createdAt.toISOString(),
  }));

  return NextResponse.json({ success: true, releases });
}
