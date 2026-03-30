import { NextResponse } from "next/server";

/**
 * 兼容旧书签/外链的 `/auth/callback`：直接重定向。
 * OAuth 主流程由 Auth.js 处理（`/api/auth/callback/*`）。
 */
export async function GET(request: Request) {
  const { searchParams, origin } = new URL(request.url);
  const next = searchParams.get("next") ?? "/console";
  return NextResponse.redirect(`${origin}${next}`);
}
