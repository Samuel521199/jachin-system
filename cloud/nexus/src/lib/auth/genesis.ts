/**
 * 零感知生根：自然人注册/首次登录后，确保存在「个人隐藏组织」+ organization_users.owner。
 * 与迁移 0012 语义一致；用于 OAuth createUser、注册 API、以及 jwt 回调中的修复路径。
 */
import { and, eq } from "drizzle-orm";
import type { getDb } from "@/db";
import { organizations, organizationUsers, users } from "@/db/schema";

export type NexusDb = NonNullable<ReturnType<typeof getDb>>;

/** 从 DB 读取用户当前个人工作区（优先 is_personal_default）的成员行 */
export async function getPersonalOrgMembership(
  db: NexusDb,
  userId: string
): Promise<{ orgId: string; role: string } | null> {
  const personal = await db
    .select({
      orgId: organizationUsers.orgId,
      role: organizationUsers.role,
    })
    .from(organizationUsers)
    .innerJoin(
      organizations,
      eq(organizationUsers.orgId, organizations.id)
    )
    .where(
      and(
        eq(organizationUsers.userId, userId),
        eq(organizations.isPersonalDefault, true)
      )
    )
    .limit(1);
  if (personal[0]) {
    return { orgId: personal[0].orgId, role: personal[0].role };
  }
  const anyRow = await db
    .select({
      orgId: organizationUsers.orgId,
      role: organizationUsers.role,
    })
    .from(organizationUsers)
    .where(eq(organizationUsers.userId, userId))
    .limit(1);
  if (anyRow[0]) {
    return { orgId: anyRow[0].orgId, role: anyRow[0].role };
  }
  return null;
}

/**
 * 若用户尚无任何 organization_users 行，则在单事务内插入个人组织 + owner。
 */
export async function ensurePersonalWorkspace(
  db: NexusDb,
  userId: string
): Promise<{ orgId: string; role: string }> {
  const existing = await getPersonalOrgMembership(db, userId);
  if (existing) return existing;

  return await db.transaction(async (tx) => {
    const [org] = await tx
      .insert(organizations)
      .values({
        name: "Personal Workspace",
        billingPlan: "free",
        isPersonalDefault: true,
      })
      .returning({ id: organizations.id });
    if (!org) throw new Error("ensurePersonalWorkspace: insert organizations failed");

    await tx.insert(organizationUsers).values({
      orgId: org.id,
      userId,
      role: "owner",
    });

    return { orgId: org.id, role: "owner" };
  });
}

/** 注册 API：单事务插入 users + 个人组织 + owner（零感知生根） */
export async function registerUserWithGenesis(
  db: NexusDb,
  params: { email: string; passwordHash: string; name?: string }
): Promise<{ userId: string; orgId: string }> {
  const id = crypto.randomUUID();
  return await db.transaction(async (tx) => {
    await tx.insert(users).values({
      id,
      email: params.email,
      name: params.name ?? null,
      passwordHash: params.passwordHash,
    });
    const [org] = await tx
      .insert(organizations)
      .values({
        name: "Personal Workspace",
        billingPlan: "free",
        isPersonalDefault: true,
      })
      .returning({ id: organizations.id });
    if (!org) throw new Error("registerUserWithGenesis: insert organizations failed");
    await tx.insert(organizationUsers).values({
      orgId: org.id,
      userId: id,
      role: "owner",
    });
    return { userId: id, orgId: org.id };
  });
}
