/**
 * 组织 / 设备组角色在界面上的中文说明（与 schema 枚举一致，供控制台展示；业务校验仍以 org-constants + DB 为准）。
 */
import { ORG_ROLES_ALL } from "@/lib/org-constants";

export const ORG_ROLE_LABELS: Record<(typeof ORG_ROLES_ALL)[number], string> = {
  owner: "所有者",
  admin: "管理员",
  member: "成员",
  fleet_admin: "车队管理员",
  viewer: "只读",
};

export const ORG_ROLE_DESCRIPTIONS: Record<(typeof ORG_ROLES_ALL)[number], string> = {
  owner:
    "组织最高权限：管理成员与角色、发放邀请、车队与订阅边界以租户为准；个人默认工作区创建即为所有者。",
  admin: "可管理成员（除产生第二个 owner）、发放邀请；车队与商店等写操作通常可用（具体以接口校验为准）。",
  member: "正式成员：可访问当前工作区下已授权的控制台能力；敏感管理操作受限。",
  fleet_admin: "侧重边缘设备 / 车队：可管理配对范围内的设备与部署；组织级计费与成员管理通常受限。",
  viewer: "只读：可查看成员列表、舰队状态等，不可改角色或下发变更。",
};

export const DEVICE_GROUP_ROLE_LABELS: Record<string, string> = {
  admin: "设备组管理员",
  viewer: "设备组只读",
};

export function formatOrgRole(role: string | undefined | null): string {
  if (!role) return "—";
  const r = role as (typeof ORG_ROLES_ALL)[number];
  return ORG_ROLE_LABELS[r] ?? role;
}

export function formatDeviceGroupRole(role: string | undefined | null): string {
  if (!role) return "—";
  return DEVICE_GROUP_ROLE_LABELS[role] ?? role;
}
