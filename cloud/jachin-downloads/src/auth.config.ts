import type { NextAuthConfig } from "next-auth";

export function resolveAuthSecret(): string | undefined {
  const fromEnv = process.env.AUTH_SECRET?.trim();
  if (fromEnv) return fromEnv;
  if (process.env.NODE_ENV !== "production") {
    return "jachin-downloads-dev-auth-secret-not-for-production";
  }
  return undefined;
}

/**
 * Download Center：除登录页、NextAuth 路由、Tauri 更新 API 外一律需登录。
 */
export const authConfig = {
  trustHost: true,
  secret: resolveAuthSecret(),
  providers: [],
  session: { strategy: "jwt" as const, maxAge: 30 * 24 * 60 * 60 },
  pages: { signIn: "/login" },
  callbacks: {
    authorized: async ({ auth, request }) => {
      const p = request.nextUrl.pathname;
      if (p.startsWith("/api/auth")) return true;
      if (p.startsWith("/api/v1/update/desktop")) return true;
      if (p === "/login") return true;
      if (
        p.startsWith("/_next") ||
        p === "/favicon.ico" ||
        /\.(?:ico|png|jpg|jpeg|svg|webp|gif)$/i.test(p)
      ) {
        return true;
      }
      return !!auth?.user;
    },
  },
} satisfies NextAuthConfig;
