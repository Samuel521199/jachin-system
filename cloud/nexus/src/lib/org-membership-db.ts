import { and, eq } from "drizzle-orm";
import type { NexusDb } from "@/lib/tenant";
import { organizations, organizationUsers } from "@/db/schema";

/** 返回用户在组织内的当前角色；非成员返回 `null`。 */
export async function getOrgMembershipRole(
  db: NexusDb,
  userId: string,
  orgId: string
): Promise<string | null> {
  const [row] = await db
    .select({ role: organizationUsers.role })
    .from(organizationUsers)
    .where(
      and(
        eq(organizationUsers.orgId, orgId),
        eq(organizationUsers.userId, userId)
      )
    )
    .limit(1);
  return row?.role ?? null;
}

/** 用户加入的全部组织（切换当前工作区 / 控制台列表用）。 */
export async function listOrganizationsForUser(
  db: NexusDb,
  userId: string
): Promise<
  Array<{
    orgId: string;
    role: string;
    name: string;
    isPersonalDefault: boolean;
  }>
> {
  const rows = await db
    .select({
      orgId: organizationUsers.orgId,
      role: organizationUsers.role,
      name: organizations.name,
      isPersonalDefault: organizations.isPersonalDefault,
    })
    .from(organizationUsers)
    .innerJoin(
      organizations,
      eq(organizations.id, organizationUsers.orgId)
    )
    .where(eq(organizationUsers.userId, userId));
  return rows;
}
