/**
 * 防弹罩：Default Deny — 由 `auth.config.ts` 中 `callbacks.authorized` 判定。
 * 使用 `auth.edge`（仅依赖轻量 `auth.config`），避免把 Drizzle/bcrypt 打进 Edge。
 *
 * - 公开：/、/login、/auth/*、/api/auth/*、/api/v1/webhooks/*、全部其它 /api/*（L2/机器流量在路由内自验 Bearer）
 * - 其余页面需有效 Session（Auth.js JWT）
 */
export { auth as middleware } from "@/auth.edge";

export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)",
  ],
};
