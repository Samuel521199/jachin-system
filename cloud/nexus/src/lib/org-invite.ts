/**
 * 极简魔法邀请：短效 HS256 JWT，与 Auth.js 会话 JWT 分离（claims 含 typ=nexus_org_invite）。
 * 密钥优先 `NEXUS_ORG_INVITE_SECRET`，否则回退与 Auth.js 一致的 {@link resolveAuthSecret}（含 dev 占位）。
 */
import { SignJWT, jwtVerify } from "jose";
import { resolveAuthSecret } from "@/auth.config";

export const ORG_INVITE_JWT_TYP = "nexus_org_invite";

export type OrgInvitePayload = {
  typ: typeof ORG_INVITE_JWT_TYP;
  /** organizations.id */
  org_id: string;
  /** 被邀请者加入后写入 organization_users.role（不可为 owner） */
  invited_role: string;
};

function getInviteSecretKey(): Uint8Array {
  const raw =
    process.env.NEXUS_ORG_INVITE_SECRET?.trim() || resolveAuthSecret() || "";
  if (!raw) {
    throw new Error(
      "NEXUS_ORG_INVITE_SECRET or AUTH_SECRET is required for org invites (production)"
    );
  }
  return new TextEncoder().encode(raw);
}

/** 签发组织邀请 Token（默认 15 分钟有效） */
export async function signOrgInviteToken(params: {
  orgId: string;
  invitedRole: string;
  /** 秒，默认 900 */
  expiresInSec?: number;
}): Promise<string> {
  const key = getInviteSecretKey();
  const exp = Math.floor(Date.now() / 1000) + (params.expiresInSec ?? 900);
  return await new SignJWT({
    typ: ORG_INVITE_JWT_TYP,
    org_id: params.orgId,
    invited_role: params.invitedRole,
  } satisfies OrgInvitePayload)
    .setProtectedHeader({ alg: "HS256" })
    .setIssuedAt()
    .setExpirationTime(exp)
    .sign(key);
}

export type VerifyOrgInviteOk = { payload: OrgInvitePayload };
export type VerifyOrgInviteErr = { error: string; message: string };

/** 校验魔法邀请 JWT，成功返回 payload */
export async function verifyOrgInviteToken(
  token: string
): Promise<VerifyOrgInviteOk | VerifyOrgInviteErr> {
  try {
    const key = getInviteSecretKey();
    const { payload } = await jwtVerify(token, key, {
      algorithms: ["HS256"],
    });
    const typ = payload.typ;
    const org_id = payload.org_id;
    const invited_role = payload.invited_role;
    if (typ !== ORG_INVITE_JWT_TYP) {
      return { error: "INVALID_TOKEN", message: "Not an organization invite token" };
    }
    if (typeof org_id !== "string" || typeof invited_role !== "string") {
      return { error: "INVALID_TOKEN", message: "Malformed invite claims" };
    }
    return {
      payload: {
        typ: ORG_INVITE_JWT_TYP,
        org_id,
        invited_role,
      },
    };
  } catch {
    return { error: "INVALID_OR_EXPIRED", message: "邀请链接无效或已过期" };
  }
}
