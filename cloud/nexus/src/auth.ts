import NextAuth from "next-auth";
import type { NextAuthConfig } from "next-auth";
import { DrizzleAdapter } from "@auth/drizzle-adapter";
import Credentials from "next-auth/providers/credentials";
import GitHub from "next-auth/providers/github";
import bcrypt from "bcryptjs";
import { eq, sql } from "drizzle-orm";
import { authConfig } from "@/auth.config";
import { getDb } from "@/db";
import { accounts, sessions, users, verificationTokens } from "@/db/schema";
import { getOrgMembershipRole, listOrganizationsForUser } from "@/lib/org-membership-db";
import { pickSessionDefaultOrg } from "@/lib/l1-workspace-context";
import { passwordPlainForCredentials } from "@/lib/auth/credentials-password";
import { credentialsHashUsable } from "@/lib/auth/password-hash";

function buildAdapter() {
  const db = getDb();
  if (!db) return undefined;
  return DrizzleAdapter(db, {
    usersTable: users,
    accountsTable: accounts,
    sessionsTable: sessions,
    verificationTokensTable: verificationTokens,
  });
}

export const { handlers, auth, signIn, signOut, unstable_update } = NextAuth({
  ...authConfig,
  adapter: buildAdapter(),
  providers: [
    Credentials({
      id: "credentials",
      name: "Email & Password",
      credentials: {
        email: { label: "Email", type: "email" },
        password: { label: "Password", type: "password" },
      },
      async authorize(credentials) {
        const emailRaw = credentials?.email;
        const password = passwordPlainForCredentials(credentials?.password);
        if (typeof emailRaw !== "string" || !password) {
          return null;
        }
        const normalizedEmail = emailRaw.trim().toLowerCase();
        if (!normalizedEmail) {
          return null;
        }
        try {
          const db = getDb();
          if (!db) {
            console.error(
              "[auth.credentials] getDb() 为空（未配置 DATABASE_URL 或连接未初始化）"
            );
            return null;
          }
          // 库中 email 若含首尾空格，eq 会匹配失败；与注册写入的 trim+lower 对齐
          const [u] = await db
            .select()
            .from(users)
            .where(sql`lower(trim(${users.email})) = ${normalizedEmail}`)
            .limit(1);
          if (!u) {
            console.warn("[auth.credentials] 无此邮箱（已 lower+trim 匹配）:", normalizedEmail);
            return null;
          }
          const storedHash = (u.passwordHash ?? "").trim();
          if (!credentialsHashUsable(storedHash)) {
            console.warn(
              "[auth.credentials] password_hash 非可用 bcrypt，len=",
              storedHash.length,
              "prefix=",
              storedHash.slice(0, 7),
              "email=",
              normalizedEmail
            );
            return null;
          }
          const ok = await bcrypt.compare(password, storedHash);
          if (!ok) {
            console.warn("[auth.credentials] bcrypt 不匹配 email=", normalizedEmail);
            return null;
          }
          return {
            id: u.id,
            name: u.name ?? undefined,
            email: normalizedEmail,
            image: u.image ?? undefined,
          };
        } catch (e) {
          console.error("[auth.credentials] authorize 异常（易被误判为密码错误）:", e);
          return null;
        }
      },
    }),
    ...(process.env.AUTH_GITHUB_ID && process.env.AUTH_GITHUB_SECRET
      ? [
          GitHub({
            allowDangerousEmailAccountLinking: true,
          }),
        ]
      : []),
  ],
  callbacks: {
    ...authConfig.callbacks,
    async jwt({ token, user, trigger, session }) {
      if (user) {
        if (user.email) token.email = user.email;
        if (user.name) token.name = user.name;
        if (user.image) token.picture = user.image;
      }
      const uid = (user?.id ?? token.sub) as string | undefined;
      if (!uid) return token;

      if (trigger === "update") {
        const nextOrg =
          session &&
          typeof session === "object" &&
          typeof (session as { activeOrgId?: string }).activeOrgId ===
            "string"
            ? (session as { activeOrgId: string }).activeOrgId.trim()
            : "";
        if (nextOrg) {
          const db = getDb();
          if (db) {
            try {
              const role = await getOrgMembershipRole(db, uid, nextOrg);
              if (role) {
                token.orgId = nextOrg;
                token.orgRole = role;
              }
            } catch (e) {
              console.error("[auth.jwt] getOrgMembershipRole 失败（不应拆登录）:", e);
            }
          }
        }
        return token;
      }

      const db = getDb();
      if (!db) return token;
      let rows: Awaited<ReturnType<typeof listOrganizationsForUser>>;
      try {
        rows = await listOrganizationsForUser(db, uid);
      } catch (e) {
        console.error(
          "[auth.jwt] listOrganizationsForUser 失败；若此处抛错，NextAuth 会报 CredentialsSignin，前端误显「密码错误」:",
          e
        );
        token.orgId = "";
        token.orgRole = "";
        return token;
      }
      if (!rows.length) {
        token.orgId = "";
        token.orgRole = "";
        return token;
      }
      const currentId =
        typeof token.orgId === "string" && token.orgId.trim()
          ? token.orgId.trim()
          : "";
      const still = currentId
        ? rows.find((r) => r.orgId === currentId)
        : undefined;
      if (still) {
        token.orgId = still.orgId;
        token.orgRole = still.role;
        return token;
      }
      const pick = pickSessionDefaultOrg(rows);
      if (pick) {
        token.orgId = pick.orgId;
        token.orgRole = pick.role;
      }
      return token;
    },
    async session({ session, token }) {
      if (session.user) {
        session.user.id = (token.sub as string) ?? "";
        session.user.orgId = (token.orgId as string) ?? "";
        session.user.orgRole = (token.orgRole as string) ?? "";
        // JWT 策略下需显式从 token 带回，否则客户端 useSession() 里 user.email/name 为空
        const email = token.email as string | undefined | null;
        const name = token.name as string | undefined | null;
        const picture = token.picture as string | undefined | null;
        if (email) session.user.email = email;
        if (name) session.user.name = name;
        if (picture) session.user.image = picture;
      }
      return session;
    },
  },
  events: {},
} satisfies NextAuthConfig);
