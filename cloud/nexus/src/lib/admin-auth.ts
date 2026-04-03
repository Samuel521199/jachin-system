/**
 * Admin API 权限校验：仅 isRoot 用户可访问 /api/v1/admin/*
 *
 * 当前实现：X-Admin-Token 与 NEXUS_ADMIN_SECRET 匹配即视为 root。
 * 可增强：`auth()` + `users.is_root` 与平台管理员策略组合（与主站 Auth.js 会话并存时需统一设计）。
 */
import { NextResponse } from "next/server";

const ADMIN_SECRET = process.env.NEXUS_ADMIN_SECRET ?? "";

/**
 * 校验请求是否来自 root 管理员。
 * 返回 null 表示通过；返回 NextResponse 表示未授权，直接 return 该响应。
 */
export function requireIsRoot(request: Request): NextResponse | null {
  if (!ADMIN_SECRET) {
    console.warn("[admin-auth] NEXUS_ADMIN_SECRET 未配置，拒绝所有 admin 请求");
    return NextResponse.json(
      { success: false, error: "FORBIDDEN", message: "管理员接口未配置" },
      { status: 403 }
    );
  }

  const cookie = request.headers.get("cookie") ?? "";
  const cookieToken = cookie.split(";").find((c) => c.trim().startsWith("nexus_admin_token="))?.split("=")[1]?.trim();
  const token =
    request.headers.get("X-Admin-Token") ??
    request.headers.get("Authorization")?.replace(/^Bearer\s+/i, "").trim() ??
    cookieToken;
  if (!token || token !== ADMIN_SECRET) {
    return NextResponse.json(
      { success: false, error: "FORBIDDEN", message: "需要 root 管理员权限" },
      { status: 403 }
    );
  }

  return null;
}
