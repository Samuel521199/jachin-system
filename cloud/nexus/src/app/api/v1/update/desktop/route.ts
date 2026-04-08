import { NextRequest, NextResponse } from "next/server";
import { desc } from "drizzle-orm";
import semver from "semver";
import { getDb, isDatabaseConfigured } from "@/db";
import { desktopAppReleases } from "@/db/schema";
import { extractBearerTokenRaw } from "@/lib/edge-agent-manifest-auth";
import { findActiveEdgeAgentByBearerToken } from "@/lib/edge-bearer";
import { presignDesktopArtifactGetUrl, isDesktopReleasesS3Configured } from "@/lib/desktop-releases-s3";
import { tauriPlatformKeyFromParts } from "@/lib/desktop-releases-common";

export const dynamic = "force-dynamic";

/**
 * GET /api/v1/update/desktop?target=windows&arch=x86_64&current_version=0.8.16
 * Tauri updater：Authorization: Bearer &lt;access_token&gt;（与 L2 nexus_config 一致）。
 * 无新版本：204 No Content。
 */
export async function GET(request: NextRequest) {
  const bearer = extractBearerTokenRaw(request);
  if (!bearer) {
    return NextResponse.json(
      { error: "需要 Bearer access_token（~/.jachin/nexus_config.json）" },
      { status: 401 }
    );
  }

  if (!isDatabaseConfigured()) {
    return new NextResponse(null, { status: 204 });
  }

  const db = getDb()!;
  const agent = await findActiveEdgeAgentByBearerToken(db, bearer);
  if (!agent) {
    return NextResponse.json({ error: "无效或过期的边缘凭证" }, { status: 401 });
  }

  if (!isDesktopReleasesS3Configured()) {
    return NextResponse.json(
      { error: "服务端未配置 DESKTOP_RELEASES_S3_*" },
      { status: 503 }
    );
  }

  const { searchParams } = new URL(request.url);
  const target = (searchParams.get("target") ?? "").trim();
  const arch = (searchParams.get("arch") ?? "").trim();
  const currentVersion = (searchParams.get("current_version") ?? "").trim();
  if (!target || !arch || !currentVersion) {
    return NextResponse.json(
      { error: "缺少 target、arch 或 current_version" },
      { status: 400 }
    );
  }

  const platformKey = tauriPlatformKeyFromParts(target, arch);

  const rows = await db
    .select({
      version: desktopAppReleases.version,
      notes: desktopAppReleases.notes,
      pubDate: desktopAppReleases.pubDate,
      artifacts: desktopAppReleases.artifacts,
    })
    .from(desktopAppReleases)
    .orderBy(desc(desktopAppReleases.pubDate));

  const valid = rows.filter((r) => semver.valid(semver.coerce(r.version) ?? r.version));
  if (!valid.length) {
    return new NextResponse(null, { status: 204 });
  }

  valid.sort((a, b) =>
    semver.rcompare(
      semver.clean(a.version) ?? a.version,
      semver.clean(b.version) ?? b.version
    )
  );
  const latest = valid[0]!;
  const latestSem = semver.clean(latest.version) ?? latest.version;
  const curSem =
    semver.valid(currentVersion) ??
    semver.valid(semver.coerce(currentVersion)) ??
    semver.clean(currentVersion);
  if (!curSem || semver.compare(latestSem, curSem) <= 0) {
    return new NextResponse(null, { status: 204 });
  }

  const art = latest.artifacts[platformKey];
  if (!art?.objectKey || !art.signature) {
    return NextResponse.json(
      {
        error: `最新版本 ${latest.version} 暂无平台 ${platformKey} 的产物，请稍后再试或从下载站获取其他包`,
      },
      { status: 404 }
    );
  }

  const url = await presignDesktopArtifactGetUrl(art.objectKey);
  if (!url) {
    return NextResponse.json({ error: "预签名失败" }, { status: 500 });
  }

  const body = {
    version: latest.version,
    notes: latest.notes ?? "",
    pub_date: latest.pubDate.toISOString(),
    platforms: {
      [platformKey]: {
        signature: art.signature,
        url,
      },
    },
  };

  return NextResponse.json(body, {
    headers: { "Cache-Control": "no-store" },
  });
}
