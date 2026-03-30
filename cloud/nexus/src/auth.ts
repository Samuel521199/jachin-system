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
import {
  ensurePersonalWorkspace,
  getPersonalOrgMembership,
} from "@/lib/auth/genesis";
import { getOrgMembershipRole } from "@/lib/org-membership-db";

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

      if (token.orgId && trigger !== "signIn") {
        return token;
      }

      const db = getDb();
      if (!db) return token;
      let mem = await getPersonalOrgMembership(db, uid);
      if (!mem) {
        mem = await ensurePersonalWorkspace(db, uid);
      }
      token.orgId = mem.orgId;
      token.orgRole = mem.role;
      return token;
    },
    async session({ session, token }) {
      if (session.user) {
        session.user.id = (token.sub as string) ?? "";
        session.user.orgId = (token.orgId as string) ?? "";
        session.user.orgRole = (token.orgRole as string) ?? "";
      }
      return session;
    },
  },
  events: {
    async createUser({ user }) {
      const db = getDb();
      if (!db || !user.id) return;
      await ensurePersonalWorkspace(db, user.id);
    },
  },
} satisfies NextAuthConfig);
