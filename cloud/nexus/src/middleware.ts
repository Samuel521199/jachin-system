import { NextResponse } from "next/server";

/**
 * 已脱离 Supabase Auth，保护路径暂不强制登录。
 * 后续可接入 Auth.js 实现鉴权。
 */
export async function middleware() {
  return NextResponse.next();
}

export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|api/).*)",
  ],
};
