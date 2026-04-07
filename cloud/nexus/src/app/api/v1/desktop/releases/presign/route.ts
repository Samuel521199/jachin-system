import { NextRequest, NextResponse } from "next/server";
import { eq } from "drizzle-orm";
import { getToken } from "next-auth/jwt";
import { resolveAuthSecret } from "@/auth.config";
import { getDb, isDatabaseConfigured } from "@/db";
import { desktopAppReleases } from "@/db/schema";
import { presignDesktopArtifactGetUrl, isDesktopReleasesS3Configured } from "@/lib/desktop-releases-s3";

export const dynamic = "force-dynamic";

/**
 * GET /api/v1/desktop/releases/presign?version=0.8.17&platform=windows-x86_64
 * 已登录用户：返回短效下载 URL（302 或 JSON url 字段）。
 */
export async function GET(request: NextRequest) {
  const secret = resolveAuthSecret();
  if (!secret) {
    return NextResponse.json(
      { success: false, error: "CONFIG", message: "AUTH_SECRET 未配置" },
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

  const { searchParams } = new URL(request.url);
  const version = (searchParams.get("version") ?? "").trim();
  const platform = (searchParams.get("platform") ?? "").trim();
  if (!version || !platform) {
    return NextResponse.json(
      { success: false, error: "BAD_REQUEST", message: "缺少 version 或 platform" },
      { status: 400 }
    );
  }

  if (!isDatabaseConfigured()) {
    return NextResponse.json(
      { success: false, error: "DATABASE_UNAVAILABLE", message: "未配置 DATABASE_URL" },
      { status: 503 }
    );
  }

  if (!isDesktopReleasesS3Configured()) {
    return NextResponse.json(
      {
        success: false,
        error: "STORAGE_UNAVAILABLE",
        message: "未配置 DESKTOP_RELEASES_S3_*，无法生成预签名链接",
      },
      { status: 503 }
    );
  }

  const db = getDb()!;
  const [row] = await db
    .select({ artifacts: desktopAppReleases.artifacts })
    .from(desktopAppReleases)
    .where(eq(desktopAppReleases.version, version))
    .limit(1);

  if (!row) {
    return NextResponse.json(
      { success: false, error: "NOT_FOUND", message: "未找到该版本" },
      { status: 404 }
    );
  }

  const meta = row.artifacts[platform];
  if (!meta?.objectKey) {
    return NextResponse.json(
      { success: false, error: "NOT_FOUND", message: "该平台无构建产物" },
      { status: 404 }
    );
  }

  const url = await presignDesktopArtifactGetUrl(meta.objectKey);
  if (!url) {
    return NextResponse.json(
      { success: false, error: "PRESIGN_FAILED", message: "无法生成预签名 URL" },
      { status: 500 }
    );
  }

  const redirect = searchParams.get("redirect");
  if (redirect === "1" || redirect === "true") {
    return NextResponse.redirect(url);
  }

  return NextResponse.json({
    success: true,
    url,
    expires_in_seconds: 900,
    object_key: meta.objectKey,
  });
}
