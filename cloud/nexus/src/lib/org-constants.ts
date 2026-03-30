/**
 * 与 `schema.ts` 中 `org_role` 枚举一致；用于 RBAC 与邀请可分配角色校验。
 */
export const ORG_ROLES_ALL = [
  "owner",
  "admin",
  "member",
  "fleet_admin",
  "viewer",
] as const;

export type OrgRole = (typeof ORG_ROLES_ALL)[number];

/** 魔法邀请可写入的目标角色（禁止通过邀请产生第二个 owner） */
export const ORG_ROLES_INVITABLE: readonly OrgRole[] = [
  "admin",
  "member",
  "fleet_admin",
  "viewer",
];

/** 可发放邀请 */
export const ORG_ROLES_CAN_INVITE: readonly OrgRole[] = ["owner", "admin"];

/** 可修改他人成员角色 */
export const ORG_ROLES_CAN_MANAGE_MEMBERS: readonly OrgRole[] = [
  "owner",
  "admin",
];
