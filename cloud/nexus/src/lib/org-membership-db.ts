import { and, eq, isNotNull, or, sql } from "drizzle-orm";
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
    slug: string | null;
    isPersonalDefault: boolean;
  }>
> {
  const rows = await db
    .select({
      orgId: organizationUsers.orgId,
      role: organizationUsers.role,
      name: organizations.name,
      slug: organizations.slug,
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

/** 边缘凭证用户在其成员工作区内按 slug 或显示名（不区分大小写、整串匹配）解析组织 */
export async function getOrganizationBySlugForUser(
  db: NexusDb,
  userId: string,
  slug: string
): Promise<{ orgId: string; name: string; slug: string | null } | null> {
  const rows = await db
    .select({
      orgId: organizations.id,
      name: organizations.name,
      slug: organizations.slug,
    })
    .from(organizations)
    .innerJoin(
      organizationUsers,
      eq(organizationUsers.orgId, organizations.id)
    )
    .where(
      and(
        eq(organizationUsers.userId, userId),
        or(
          and(isNotNull(organizations.slug), eq(organizations.slug, slug)),
          sql`lower(trim(${organizations.name}::text)) = ${slug}`
        )
      )
    )
    .limit(10);

  if (rows.length === 0) return null;
  const ids = new Set(rows.map((r) => r.orgId));
  if (ids.size !== 1) return null;
  return rows[0] ?? null;
}
