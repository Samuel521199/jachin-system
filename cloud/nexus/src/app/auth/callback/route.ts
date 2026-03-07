import { NextResponse } from "next/server";

/**
 * OAuth/OTP 回调直接重定向到控制台。
 * 后续可接入 Auth.js 实现回调处理。
 */
export async function GET(request: Request) {
  const { searchParams, origin } = new URL(request.url);
  const next = searchParams.get("next") ?? "/console";
  return NextResponse.redirect(`${origin}${next}`);
}
