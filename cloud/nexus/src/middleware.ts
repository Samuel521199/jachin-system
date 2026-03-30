import { NextResponse } from "next/server";

/**
 * 保护路径暂不强制登录。
 * 后续可接入 Auth.js 实现鉴权。
 */
export async function middleware() {
  return NextResponse.next();
}

export const config = {
  matcher: [
    // packages/*.zip 由 Route Handler 直出，避免与中间件链冲突
    "/((?!_next/static|_next/image|favicon.ico|api/|packages/).*)",
  ],
};
