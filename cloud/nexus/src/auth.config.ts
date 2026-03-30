import type { NextAuthConfig } from "next-auth";

/**
 * Edge 与 Node 共用的「轻量」配置：仅 trustHost / session / pages / authorized。
 * 禁止在此文件引入 DB、bcrypt、Drizzle —— middleware 只应依赖本文件 + `auth.edge.ts`，
 * 避免 bcrypt/postgres 被打进 Edge 包。
 *
 * 登录、JWT 注入 org、OAuth、adapter 等在 `auth.ts` 中合并扩展。
 */
export const authConfig = {
  trustHost: true,
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
      return !!auth?.user;
    },
  },
} satisfies NextAuthConfig;
