import { NextRequest, NextResponse } from "next/server";
import { requireIsRoot } from "@/lib/admin-auth";

export const dynamic = "force-dynamic";

/**
 * GET /api/v1/admin/check
 * 校验当前请求是否具有 root 权限，用于前端解锁审核面板。
 */
export async function GET(request: NextRequest) {
  const forbidden = requireIsRoot(request);
  if (forbidden) return forbidden;
  return NextResponse.json({ ok: true });
}
