import { NextResponse } from "next/server";
import { eq } from "drizzle-orm";
import { getDb, isDatabaseConfigured } from "@/db";
import { desktopAppReleases, type DesktopArtifactMeta } from "@/db/schema";
import { requireIsRoot } from "@/lib/admin-auth";
import {
  normalizeArtifactSignatureForStorage,
  validateArtifactSignatureMatchesDeclaredVersion,
  validateObjectKeyContainsVersionSegment,
} from "@/lib/desktop-releases-common";

export const dynamic = "force-dynamic";

type PostBody = {
  version: string;
  notes?: string;
  pub_date: string;
  artifacts: Record<string, DesktopArtifactMeta>;
};

/**
 * POST /api/v1/admin/desktop-releases
 * 登记新版本元数据（对象需已上传至私有 Bucket）。仅 NEXUS_ADMIN_SECRET。
 */
export async function POST(request: Request) {
  const denied = requireIsRoot(request);
  if (denied) return denied;

  if (!isDatabaseConfigured()) {
    return NextResponse.json(
      { success: false, error: "DATABASE_UNAVAILABLE", message: "未配置 DATABASE_URL" },
      { status: 503 }
    );
  }

  let body: PostBody;
  try {
    body = (await request.json()) as PostBody;
  } catch {
    return NextResponse.json(
      { success: false, error: "BAD_REQUEST", message: "无效 JSON" },
      { status: 400 }
    );
  }

  const version = (body.version ?? "").trim();
  const pubDateRaw = (body.pub_date ?? "").trim();
  const artifacts = body.artifacts;
  if (!version || !pubDateRaw || !artifacts || typeof artifacts !== "object") {
    return NextResponse.json(
      {
        success: false,
        error: "BAD_REQUEST",
        message: "需要 version、pub_date（ISO）、artifacts",
      },
      { status: 400 }
    );
  }

  const pubDate = new Date(pubDateRaw);
  if (Number.isNaN(pubDate.getTime())) {
    return NextResponse.json(
      { success: false, error: "BAD_REQUEST", message: "pub_date 无效" },
      { status: 400 }
    );
  }

  for (const [k, v] of Object.entries(artifacts)) {
    if (!k.trim() || !v?.objectKey?.trim() || !v?.signature?.trim()) {
      return NextResponse.json(
        {
          success: false,
          error: "BAD_REQUEST",
          message: `artifacts[${k}] 需要 objectKey 与 signature`,
        },
        { status: 400 }
      );
    }
  }

  const artifactsNormalized: Record<string, DesktopArtifactMeta> = Object.fromEntries(
    Object.entries(artifacts).map(([k, v]) => [
      k,
      {
        objectKey: v.objectKey.trim(),
        signature: normalizeArtifactSignatureForStorage(v.signature),
      },
    ])
  );

  for (const [plat, meta] of Object.entries(artifactsNormalized)) {
    const keyChk = validateObjectKeyContainsVersionSegment(meta.objectKey, version);
    if (!keyChk.ok) {
      return NextResponse.json(
        {
          success: false,
          error: "OBJECT_KEY_VERSION_MISMATCH",
          message: `[${plat}] ${keyChk.message}`,
        },
        { status: 400 }
      );
    }
    const sigChk = validateArtifactSignatureMatchesDeclaredVersion(meta.signature, version);
    if (!sigChk.ok) {
      return NextResponse.json(
        {
          success: false,
          error: "SIGNATURE_VERSION_MISMATCH",
          message: `[${plat}] ${sigChk.message}`,
        },
        { status: 400 }
      );
    }
  }

  const db = getDb()!;
  const [existing] = await db
    .select({ id: desktopAppReleases.id })
    .from(desktopAppReleases)
    .where(eq(desktopAppReleases.version, version))
    .limit(1);

  if (existing) {
    await db
      .update(desktopAppReleases)
      .set({
        notes: body.notes ?? null,
        pubDate,
        artifacts: artifactsNormalized,
      })
      .where(eq(desktopAppReleases.version, version));
    return NextResponse.json({ success: true, updated: true, version });
  }

  await db.insert(desktopAppReleases).values({
    version,
    notes: body.notes ?? null,
    pubDate,
    artifacts: artifactsNormalized,
  });

  return NextResponse.json({ success: true, updated: false, version });
}
