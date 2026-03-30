import fs from "fs";
import path from "path";
import { type NextRequest, NextResponse } from "next/server";

/**
 * standalone 下运行时写入的 public/packages/*.zip 未必被默认静态层暴露，导致 L2 拉包 404。
 * 显式提供 GET /packages/<file>.zip，与 savePackageLocally 写入路径一致。
 */
export async function GET(
  _request: NextRequest,
  context: { params: { path: string[] } }
) {
  const segments = context.params?.path;
  if (!segments?.length) {
    return new NextResponse("Not Found", { status: 404 });
  }
  for (const s of segments) {
    if (s === ".." || s === "." || s.includes("/") || s.includes("\\")) {
      return new NextResponse("Bad path", { status: 400 });
    }
  }

  const baseDir = path.join(process.cwd(), "public", "packages");
  const resolvedBase = path.resolve(baseDir);
  const candidate = path.resolve(baseDir, ...segments);

  if (!candidate.startsWith(resolvedBase + path.sep) && candidate !== resolvedBase) {
    return new NextResponse("Bad path", { status: 400 });
  }

  try {
    if (!fs.existsSync(candidate) || !fs.statSync(candidate).isFile()) {
      return new NextResponse("Not Found", { status: 404 });
    }
    const buf = fs.readFileSync(candidate);
    return new NextResponse(buf, {
      status: 200,
      headers: {
        "Content-Type": "application/zip",
        "Cache-Control": "public, max-age=3600",
      },
    });
  } catch {
    return new NextResponse("Not Found", { status: 404 });
  }
}
