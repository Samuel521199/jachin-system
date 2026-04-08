import { NextRequest, NextResponse } from "next/server";
import { eq } from "drizzle-orm";
import { auth } from "@/auth";
import { getDb, isDatabaseConfigured } from "@/db";
import { desktopAppReleases } from "@/db/schema";
import { getDownloadUrl, isS3Configured } from "@/lib/s3";

export const dynamic = "force-dynamic";

/**
 * GET /api/downloads/generate-link?version=0.8.17&platform=windows-x86_64
 * 校验 Session 后 302 到 MinIO 预签名 URL。
 */
export async function GET(request: NextRequest) {
  const session = await auth();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "请先登录" }, { status: 401 });
  }

  if (!isDatabaseConfigured()) {
    return NextResponse.json({ error: "数据库未配置" }, { status: 503 });
  }
  if (!isS3Configured()) {
    return NextResponse.json({ error: "对象存储未配置 DESKTOP_RELEASES_S3_*" }, { status: 503 });
  }

  const { searchParams } = new URL(request.url);
  const version = (searchParams.get("version") ?? "").trim();
  const platform = (searchParams.get("platform") ?? "").trim();
  if (!version || !platform) {
    return NextResponse.json(
      { error: "缺少 version 或 platform" },
      { status: 400 }
    );
  }

  const db = getDb()!;
  const [row] = await db
    .select()
    .from(desktopAppReleases)
    .where(eq(desktopAppReleases.version, version))
    .limit(1);

  if (!row) {
    return NextResponse.json({ error: "未找到该版本" }, { status: 404 });
  }

  const meta = row.artifacts[platform];
  if (!meta?.objectKey) {
    return NextResponse.json({ error: "该平台无构建产物" }, { status: 404 });
  }

  const url = await getDownloadUrl(meta.objectKey, 900);
  if (!url) {
    return NextResponse.json({ error: "预签名失败" }, { status: 500 });
  }

  return NextResponse.redirect(url, { status: 302 });
}
