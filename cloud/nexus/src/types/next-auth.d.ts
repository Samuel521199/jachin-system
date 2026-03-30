import type { DefaultSession } from "next-auth";

declare module "next-auth" {
  interface Session {
    user: {
      id: string;
      /** organizations.id（合法 tenant_id） */
      orgId: string;
      /** organization_users.role */
      orgRole: string;
    } & DefaultSession["user"];
    /**
     * 仅用于 `update({ activeOrgId })` / `unstable_update` 传入；由 jwt 回调消费后写入 token，
     * 不会长期暴露在客户端 Session JSON 中（仍以 user.orgId 为准）。
     */
    activeOrgId?: string;
  }
}

declare module "next-auth/jwt" {
  interface JWT {
    orgId?: string;
    orgRole?: string;
  }
}
