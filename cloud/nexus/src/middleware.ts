/**
 * 防弹罩：Default Deny — 由 `auth.config.ts` 中 `callbacks.authorized` 判定。
 * 使用 `auth.edge`（仅依赖轻量 `auth.config`），避免把 Drizzle/bcrypt 打进 Edge。
 *
 * 若用户误在浏览器打开 http://0.0.0.0:3000/...（日志里「Ready on 0.0.0.0」易误导），
 * Host 会变成 0.0.0.0，重定向与 callbackUrl 会带错误 origin。在已配置 NEXUS_PUBLIC_URL 时
 * 先 302 到公网可访问基址（路径与 query 保留）。
 *
 * - 公开：/、/login、/auth/*、/api/auth/*、/api/v1/webhooks/*、全部其它 /api/*（L2/机器流量在路由内自验 Bearer）
 * - 其余页面需有效 Session（Auth.js JWT）
 */
import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { auth } from "@/auth.edge";

function hostHeaderHostname(host: string): string {
  const h = host.trim().toLowerCase();
  if (!h) return "";
  if (h.startsWith("[")) {
    const close = h.indexOf("]");
    if (close > 1) return h.slice(1, close);
    return h;
  }
  const lastColon = h.lastIndexOf(":");
  if (lastColon > 0 && /^\d+$/.test(h.slice(lastColon + 1))) {
    return h.slice(0, lastColon);
  }
  return h;
}

function isBindAllHost(hostname: string): boolean {
  return (
    hostname === "0.0.0.0" ||
    hostname === "::" ||
    hostname === "0:0:0:0:0:0:0:0"
  );
}

function redirectOffBindAllHost(req: NextRequest): NextResponse | null {
  const raw = (process.env.NEXUS_PUBLIC_URL || "").trim().replace(/\/$/, "");
  if (!raw) return null;
  const hostHeader = req.headers.get("host") || "";
  if (!isBindAllHost(hostHeaderHostname(hostHeader))) return null;
  try {
    const base =
      raw.startsWith("http://") || raw.startsWith("https://")
        ? raw
        : `http://${raw}`;
    const target = new URL(
      `${req.nextUrl.pathname}${req.nextUrl.search}`,
      base,
    );
    return NextResponse.redirect(target);
  } catch {
    return null;
  }
}

/** NextAuth v5：`auth` 须用回调包装为中间件，不能 `auth(req)` 直接调用。 */
export default auth((req) => {
  const p = req.nextUrl.pathname;
  // 双保险：matcher 已排除公开页与 _next，但若环境对正则匹配有差异，避免误伤
  if (p === "/" || p === "/login" || p.startsWith("/auth/") || p.startsWith("/_next/")) {
    return NextResponse.next();
  }
  const early = redirectOffBindAllHost(req);
  if (early) return early;
});

export const config = {
  matcher: [
    // 排除公开页、API、Next 内部资源、静态后缀；api 用 api/|api$ 避免误伤 /apiconfig 等
    "/((?!$|login$|auth/|api/|api$|_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)",
  ],
};
