/**
 * L1 工作区上下文：会话默认 org、L2 网关可用的租户（须 owner/admin）。
 * 与 docs/ARCHITECTURE_L1_WORKSPACE_L2_GATEWAY_L3.md 对齐：注册不自动建组织。
 */
import type { NexusDb } from "@/lib/tenant";
import {
  getOrgMembershipRole,
  listOrganizationsForUser,
} from "@/lib/org-membership-db";

/** 可登录 L2 网关并审批 L3 的组织角色（L1 邮箱登录 L2） */
export const ORG_ROLES_L2_GATEWAY = ["owner", "admin"] as const;

export type OrgRow = Awaited<
  ReturnType<typeof listOrganizationsForUser>
>[number];

/** 团队工作区优先于个人默认工作区 */
export function sortOrgsForSessionDefault(rows: OrgRow[]): OrgRow[] {
  return [...rows].sort(
    (a, b) => Number(a.isPersonalDefault) - Number(b.isPersonalDefault)
  );
}

/** 选会话默认组织（任意成员角色均可） */
export function pickSessionDefaultOrg(rows: OrgRow[]): OrgRow | null {
  if (!rows.length) return null;
  return sortOrgsForSessionDefault(rows)[0] ?? null;
}

function isL2GatewayRole(role: string): boolean {
  return (ORG_ROLES_L2_GATEWAY as readonly string[]).includes(role);
}

/**
 * 解析 L2 网关绑定用的 tenant（须 owner/admin）。
 * explicitOrgId 须为用户所属且具备网关角色。
 */
export async function resolveOrganizationForL2Gateway(
  db: NexusDb,
  userId: string,
  explicitOrgId?: string | null
): Promise<{ orgId: string; role: string } | null> {
  const rows = await listOrganizationsForUser(db, userId);
  if (!rows.length) return null;

  const trimmed = explicitOrgId?.trim();
  if (trimmed) {
    const row = rows.find((r) => r.orgId === trimmed);
    if (!row || !isL2GatewayRole(row.role)) return null;
    return { orgId: row.orgId, role: row.role };
  }

  const eligible = rows.filter((r) => isL2GatewayRole(r.role));
  if (!eligible.length) return null;
  const pick = sortOrgsForSessionDefault(eligible)[0];
  return pick ? { orgId: pick.orgId, role: pick.role } : null;
}

/** 校验用户在某 org 是否具备 L2 网关角色 */
export async function userCanManageL2Gateway(
  db: NexusDb,
  userId: string,
  orgId: string
): Promise<boolean> {
  const role = await getOrgMembershipRole(db, userId, orgId);
  return role != null && isL2GatewayRole(role);
}

/** 边缘实例 / manifest：取用户默认会话工作区 UUID；无组织时返回 null（不自动创建）。 */
export async function resolveTenantIdForEdgeUser(
  db: NexusDb,
  userId: string
): Promise<string | null> {
  const rows = await listOrganizationsForUser(db, userId);
  const pick = pickSessionDefaultOrg(rows);
  return pick?.orgId ?? null;
}
