import { NextRequest, NextResponse } from "next/server";
import { and, eq } from "drizzle-orm";
import { createReadStream } from "fs";
import { stat } from "fs/promises";
import path from "path";
import { Readable } from "stream";
import { getDb, isDatabaseConfigured } from "@/db";
import { pluginsRegistry } from "@/db/schema";

export const dynamic = "force-dynamic";

function inferFileName(pluginId: string, version: string | null, packageUrl: string): string {
  const fromUrl = packageUrl.split("?")[0].split("#")[0].split("/").filter(Boolean).pop();
  if (fromUrl && fromUrl.includes(".")) return fromUrl;
  const safePlugin = pluginId.replace(/[^a-zA-Z0-9._-]/g, "-");
  const safeVer = (version ?? "latest").replace(/[^a-zA-Z0-9._-]/g, "-");
  return `${safePlugin}_v${safeVer}.zip`;
}

async function tryLocalPackageResponse(packageUrl: string, fileName: string): Promise<NextResponse | null> {
  let parsed: URL;
  try {
    parsed = packageUrl.startsWith("http://") || packageUrl.startsWith("https://")
      ? new URL(packageUrl)
      : new URL(packageUrl, "http://localhost");
  } catch {
    return null;
  }

  if (!parsed.pathname.startsWith("/packages/")) return null;

  const packageRoot = path.resolve(process.cwd(), "public", "packages");
  const rel = decodeURIComponent(parsed.pathname.slice("/packages/".length));
  const fullPath = path.resolve(packageRoot, rel);
  const rootWithSep = packageRoot.endsWith(path.sep) ? packageRoot : `${packageRoot}${path.sep}`;
  if (!fullPath.startsWith(rootWithSep)) {
    return NextResponse.json({ success: false, error: "非法模型包路径" }, { status: 400 });
  }

  const info = await stat(fullPath).catch(() => null);
  if (!info?.isFile()) {
    return NextResponse.json({ success: false, error: `模型包文件不存在: ${rel}` }, { status: 404 });
  }

  const stream = Readable.toWeb(createReadStream(fullPath)) as ReadableStream;
  return new NextResponse(stream, {
    status: 200,
    headers: {
      "Content-Type": "application/zip",
      "Content-Length": String(info.size),
      "Content-Disposition": `attachment; filename="${fileName}"`,
      "Cache-Control": "no-store",
    },
  });
}

export async function GET(request: NextRequest) {
  try {
    const pluginId = new URL(request.url).searchParams.get("model_plugin_id")?.trim();
    if (!pluginId) {
      return NextResponse.json({ success: false, error: "缺少 model_plugin_id 参数" }, { status: 400 });
    }

    if (!isDatabaseConfigured()) {
      return NextResponse.json(
        { success: false, error: "数据库未配置，无法解析模型下载地址" },
        { status: 503 }
      );
    }

    const db = getDb()!;
    const [model] = await db
      .select({
        pluginId: pluginsRegistry.pluginId,
        version: pluginsRegistry.version,
        packageUrl: pluginsRegistry.packageUrl,
      })
      .from(pluginsRegistry)
      .where(
        and(
          eq(pluginsRegistry.pluginId, pluginId),
          eq(pluginsRegistry.itemType, "MODEL"),
          eq(pluginsRegistry.visibility, "PUBLIC"),
          eq(pluginsRegistry.status, "approved")
        )
      )
      .limit(1);

    if (!model) {
      return NextResponse.json({ success: false, error: "模型不存在或未上架" }, { status: 404 });
    }

    const packageUrl = (model.packageUrl ?? "").trim();
    if (!packageUrl) {
      return NextResponse.json({ success: false, error: "模型未提供下载包地址" }, { status: 409 });
    }

    const fileName = inferFileName(model.pluginId, model.version, packageUrl);
    const localResponse = await tryLocalPackageResponse(packageUrl, fileName);
    if (localResponse) return localResponse;

    const upstream = await fetch(packageUrl);
    if (!upstream.ok || !upstream.body) {
      return NextResponse.json(
        { success: false, error: `模型下载源不可用（HTTP ${upstream.status}）` },
        { status: 502 }
      );
    }

    return new NextResponse(upstream.body, {
      status: 200,
      headers: {
        "Content-Type": upstream.headers.get("content-type") ?? "application/octet-stream",
        "Content-Disposition": `attachment; filename="${fileName}"`,
        "Cache-Control": "no-store",
      },
    });
  } catch (e) {
    console.error("[store/model-download] Unexpected error:", e);
    return NextResponse.json({ success: false, error: "Internal server error" }, { status: 500 });
  }
}
