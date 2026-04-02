import { NextResponse } from "next/server";

/** 已登录但 JWT 无 orgId 时统一 403，引导 /console/workspace */
export function jsonOrgRequiredResponse(): NextResponse {
  return NextResponse.json(
    {
      success: false,
      error: "ORG_REQUIRED",
      message:
        "请先加入或创建工作区，并在控制台切换为当前工作区（/console/workspace）",
    },
    { status: 403 }
  );
}
