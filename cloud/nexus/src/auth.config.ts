import type { NextAuthConfig } from "next-auth";

/**
 * Auth.js 要求配置 `secret`（或环境变量 AUTH_SECRET）。
 * - 生产环境（next start / 正式部署）**必须**设置 AUTH_SECRET，否则启动会失败。
 * - 本地 `next dev` 若未配 AUTH_SECRET，使用下方占位值，避免 MissingSecret。
 *
 * 业务路由里用 `getToken` 验签时 **必须**与本函数一致，禁止只读 `process.env.AUTH_SECRET`，
 * 否则未配环境变量时会话仍可用但组织 API 会 500。
 */
export function resolveAuthSecret(): string | undefined {
  const fromEnv = process.env.AUTH_SECRET?.trim();
  if (fromEnv) return fromEnv;
  if (process.env.NODE_ENV !== "production") {
    return "nexus-dev-only-auth-secret-not-for-production";
  }
  return undefined;
}

/**
 * Edge 与 Node 共用的「轻量」配置：仅 trustHost / session / pages / authorized。
 * 禁止在此文件引入 DB、bcrypt、Drizzle —— middleware 只应依赖本文件 + `auth.edge.ts`，
 * 避免 bcrypt/postgres 被打进 Edge 包。
 *
 * 登录、JWT 注入 org、OAuth、adapter 等在 `auth.ts` 中合并扩展。
 */
export const authConfig = {
  trustHost: true,
  /** 模块加载时解析一次；与运行时 {@link resolveAuthSecret} 规则一致 */
  secret: resolveAuthSecret(),
  providers: [],
  session: { strategy: "jwt", maxAge: 30 * 24 * 60 * 60 },
  pages: { signIn: "/login" },
  callbacks: {
    authorized: async ({ auth, request }) => {
      const p = request.nextUrl.pathname;
      if (p.startsWith("/api/auth")) return true;
      if (p.startsWith("/api/v1/webhooks")) return true;
      if (p.startsWith("/api/")) return true;
      if (p === "/login" || p === "/" || p.startsWith("/auth/")) return true;
      /**
       * L2 SyncDaemon 用 httpx 直链下载 manifest 里的 package_url（无浏览器 Cookie）。
       * 若此处不放行，会 307 → /login，导致 inventory/l3_mcps 永远拉不下来。
       * 包名含版本与时间戳，仍建议生产用 CDN/签名 URL；见 sync 与 store 文档。
       */
      if (p.startsWith("/packages/") && request.method === "GET") return true;
      return !!auth?.user;
    },
  },
} satisfies NextAuthConfig;
