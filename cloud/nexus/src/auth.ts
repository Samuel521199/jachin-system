import NextAuth from "next-auth";
import type { NextAuthConfig } from "next-auth";
import { DrizzleAdapter } from "@auth/drizzle-adapter";
import Credentials from "next-auth/providers/credentials";
import GitHub from "next-auth/providers/github";
import bcrypt from "bcryptjs";
import { eq } from "drizzle-orm";
import { authConfig } from "@/auth.config";
import { getDb } from "@/db";
import { accounts, sessions, users, verificationTokens } from "@/db/schema";
import { getOrgMembershipRole, listOrganizationsForUser } from "@/lib/org-membership-db";
import { pickSessionDefaultOrg } from "@/lib/l1-workspace-context";

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
        const email = credentials?.email;
        const password = credentials?.password;
        if (
          typeof email !== "string" ||
          typeof password !== "string" ||
          !email ||
          !password
        ) {
          return null;
        }
        const db = getDb();
        if (!db) return null;
        const [u] = await db
          .select()
          .from(users)
          .where(eq(users.email, email.trim().toLowerCase()))
          .limit(1);
        if (!u?.passwordHash) return null;
        const ok = await bcrypt.compare(password, u.passwordHash);
        if (!ok) return null;
        return {
          id: u.id,
          name: u.name ?? undefined,
          email: u.email ?? undefined,
          image: u.image ?? undefined,
        };
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
            const role = await getOrgMembershipRole(db, uid, nextOrg);
            if (role) {
              token.orgId = nextOrg;
              token.orgRole = role;
            }
          }
        }
        return token;
      }

      const db = getDb();
      if (!db) return token;
      const rows = await listOrganizationsForUser(db, uid);
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
