/**
 * 租户（Tenant）解析与校验 — **Organization = Tenant（SSOT）**
 *
 * - **绝对领域（业务 API）**：{@link extractTenantId} **仅**返回 Auth.js 验签会话中的 `orgId`，
 *   **绝不**采信 `X-Tenant-Id` / 机器 Header 伪造租户。
 * - **L2 / 同步桥**：{@link extractTenantIdAllowingMachineFallback} — 会话优先，无会话时再读 Header/Cookie
 *   （见 {@link extractTenantIdFromUnverifiedSources}）。
 * - 用户归属 **唯一** 依据 {@link organizationUsers}（`org_id` + `user_id`）。
 *
 * @module lib/tenant
 */
import { NextRequest } from "next/server";
import { getToken } from "next-auth/jwt";
import { resolveAuthSecret } from "@/auth.config";
import { and, eq } from "drizzle-orm";
import type { getDb } from "@/db";
import { organizations, organizationUsers } from "@/db/schema";

/** Drizzle 数据库实例（与 `getDb()` 一致） */
export type NexusDb = NonNullable<ReturnType<typeof getDb>>;

export interface TenantExtractResult {
  tenantId: string;
  source: "header" | "jwt";
}

/** PostgreSQL `uuid` / RFC4122 字符串形式（版本位不限制，兼容 gen_random_uuid） */
const UUID_ANY =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** 是否为合法 UUID 字符串（用于 `organizations.id`） */
export function isTenantUuidString(value: string): boolean {
  return UUID_ANY.test(value.trim());
}

function decodeJwtPayloadUnverified(token: string): Record<string, unknown> | null {
  try {
    const parts = token.split(".");
    if (parts.length !== 3 || !parts[1]) return null;
    const json = Buffer.from(parts[1], "base64url").toString("utf8");
    return JSON.parse(json) as Record<string, unknown>;
  } catch {
    return null;
  }
}

/**
 * 从 Authorization Bearer 中解析 JWT `sub`（通常为 `users.id`），不验证签名（与现有骨架一致）。
 * 生产环境应在网关或本处接入 `jose` 验签后再信任 claims。
 */
export function extractJwtSubjectFromRequest(request: NextRequest): string | null {
  const authHeader = request.headers.get("Authorization");
  if (!authHeader?.startsWith("Bearer ")) return null;
  const token = authHeader.slice(7).trim();
  if (!token) return null;
  const payload = decodeJwtPayloadUnverified(token);
  if (!payload) return null;
  const sub = payload.sub;
  return typeof sub === "string" && sub.length > 0 ? sub : null;
}

/**
 * **可信租户 ID**：仅来自验签后的 Auth.js JWT `orgId`；无会话或 claims 缺失则返回 `null`。
 * 组织管理等业务 API 应使用本函数（或 {@link withOrgRole}），不信任客户端 `X-Tenant-Id`。
 */
export async function extractTrustedTenantId(
  request: NextRequest
): Promise<string | null> {
  const secret = process.env.AUTH_SECRET;
  if (!secret) return null;
  try {
    const token = await getToken({ req: request, secret });
    const orgId = token?.orgId;
    if (typeof orgId === "string" && orgId.length > 0) return orgId;
  } catch {
    /* 无会话或非法 token */
  }
  return null;
}

/**
 * 从 Auth.js 会话（Cookie 或 Authorization 中的 Auth.js JWT）经 `getToken` **验签**后读取 `orgId`。
 * @alias 与 {@link extractTrustedTenantId} 同义，便于检索「Session 验签」路径。
 */
export async function extractVerifiedOrgIdFromSession(
  request: NextRequest
): Promise<string | null> {
  return extractTrustedTenantId(request);
}

/**
 * 从会话 JWT 读取 `orgRole`（与 {@link extractTrustedTenantId} 成对使用）。
 */
export async function extractVerifiedOrgRoleFromSession(
  request: NextRequest
): Promise<string | null> {
  const secret = resolveAuthSecret();
  if (!secret) return null;
  try {
    const token = await getToken({ req: request, secret });
    const r = token?.orgRole;
    if (typeof r === "string" && r.length > 0) return r;
  } catch {
    /* ignore */
  }
  return null;
}

/**
 * 从请求中**仅解析**原始 `tenant_id` 字符串（未访问数据库、**不**验签 Bearer）。
 * 优先级：`X-Tenant-Id` → Bearer 内 `tenant_id`/`org_id` → Cookie `nexus_tenant_id`
 *
 * @warning 仅用于 L2/边缘网关等无浏览器会话场景；**禁止**单独作为业务多租户写接口的凭据。
 */
export function extractTenantIdFromUnverifiedSources(
  request: NextRequest
): string | null {
  const headerTenant = request.headers.get("X-Tenant-Id")?.trim();
  if (headerTenant && headerTenant.length > 0) {
    return headerTenant;
  }

  const authHeader = request.headers.get("Authorization");
  if (authHeader?.startsWith("Bearer ")) {
    const token = authHeader.slice(7).trim();
    if (token) {
      const payload = decodeJwtPayloadUnverified(token);
      if (payload) {
        const tid = payload.tenant_id ?? payload.org_id;
        if (typeof tid === "string" && tid.length > 0) {
          return tid;
        }
      }
    }
  }

  const cookieHeader = request.headers.get("Cookie");
  if (cookieHeader) {
    const match = cookieHeader.match(/nexus_tenant_id=([^;]+)/);
    const tenantId = match?.[1]?.trim();
    if (tenantId && tenantId.length > 0) {
      return decodeURIComponent(tenantId);
    }
  }

  return null;
}

/**
 * **可信租户 ID（与 {@link extractTrustedTenantId} 相同）**：仅会话验签 `orgId`。
 * 用于业务 API 默认租户解析；L2 请改用 {@link extractTenantIdAllowingMachineFallback}。
 */
export async function extractTenantId(
  request: NextRequest
): Promise<string | null> {
  return extractTrustedTenantId(request);
}

/**
 * 会话优先；无有效会话租户时再尝试 `X-Tenant-Id` / Bearer 明文 claims / `nexus_tenant_id` Cookie。
 * 供 manifest、遥测、Store 等 **L2 桥** 路由使用。
 */
export async function extractTenantIdAllowingMachineFallback(
  request: NextRequest
): Promise<string | null> {
  const trusted = await extractTrustedTenantId(request);
  if (trusted) return trusted;
  return extractTenantIdFromUnverifiedSources(request);
}

/** 查询 `organizations` 是否存在该主键 */
export async function assertOrganizationExists(
  db: NexusDb,
  tenantId: string
): Promise<boolean> {
  const [row] = await db
    .select({ id: organizations.id })
    .from(organizations)
    .where(eq(organizations.id, tenantId))
    .limit(1);
  return Boolean(row);
}

/** 查询用户是否在 `organization_users` 中关联该组织 */
export async function assertUserMemberOfOrganization(
  db: NexusDb,
  tenantId: string,
  userId: string
): Promise<boolean> {
  const [row] = await db
    .select({ id: organizationUsers.id })
    .from(organizationUsers)
    .where(
      and(
        eq(organizationUsers.orgId, tenantId),
        eq(organizationUsers.userId, userId)
      )
    )
    .limit(1);
  return Boolean(row);
}

export type ResolveTenantResult =
  | { ok: true; tenantId: string }
  | { ok: false; status: number; error: string; message: string };

/**
 * 解析并校验租户：组织存在；若提供 `userId` 则必须为该组织成员。
 * 用于需登录用户 + 选定组织（tenant）的 API。
 *
 * @param userId 当前会话用户 `users.id`；若为 `null`（如纯机器令牌），则**仅**校验组织存在（适用于部分 L2 同步场景，风险见文档）。
 * @param options.tenantSource `trusted_session`（默认）仅信会话；`machine_bridge` 允许 Header/Cookie 回退。
 */
export async function resolveValidatedTenant(
  db: NexusDb,
  request: NextRequest,
  userId: string | null,
  options?: { tenantSource?: "trusted_session" | "machine_bridge" }
): Promise<ResolveTenantResult> {
  const source = options?.tenantSource ?? "trusted_session";
  const raw =
    source === "machine_bridge"
      ? await extractTenantIdAllowingMachineFallback(request)
      : await extractTenantId(request);
  if (!raw || !raw.trim()) {
    return {
      ok: false,
      status: 401,
      error: "Missing tenant_id",
      message:
        source === "machine_bridge"
          ? "Sign in (Auth.js session), or provide X-Tenant-Id / Bearer org_id / nexus_tenant_id cookie"
          : "Sign in with Auth.js session (trusted org_id in JWT)",
    };
  }
  const tenantId = raw.trim();
  if (!isTenantUuidString(tenantId)) {
    return {
      ok: false,
      status: 400,
      error: "Invalid tenant_id",
      message: "tenant_id must be a valid organization UUID (organizations.id)",
    };
  }

  const exists = await assertOrganizationExists(db, tenantId);
  if (!exists) {
    return {
      ok: false,
      status: 404,
      error: "Unknown tenant",
      message: "No organization with this id",
    };
  }

  if (userId) {
    const member = await assertUserMemberOfOrganization(db, tenantId, userId);
    if (!member) {
      return {
        ok: false,
        status: 403,
        error: "Forbidden",
        message: "User is not a member of this organization",
      };
    }
  }

  return { ok: true, tenantId };
}

/**
 * 从请求中提取 developer_id（供开发者收益接口鉴权）
 * 优先级：X-Developer-Id > JWT sub/developer_id
 */
export function extractDeveloperId(request: Request): string | null {
  const header = request.headers.get("X-Developer-Id")?.trim();
  if (header && header.length > 0) return header;

  const authHeader = request.headers.get("Authorization");
  if (authHeader?.startsWith("Bearer ")) {
    const token = authHeader.slice(7).trim();
    if (token) {
      try {
        const parts = token.split(".");
        if (parts.length === 3 && parts[1]) {
          const payload = JSON.parse(
            Buffer.from(parts[1], "base64url").toString("utf8")
          ) as Record<string, unknown>;
          const id =
            (payload.developer_id as string) ?? (payload.sub as string);
          if (typeof id === "string" && id.length > 0) return id;
        }
      } catch {
        /* ignore */
      }
    }
  }

  const cookieHeader = request.headers.get("Cookie");
  if (cookieHeader) {
    const match = cookieHeader.match(/nexus_developer_id=([^;]+)/);
    const id = match?.[1]?.trim();
    if (id && id.length > 0) return decodeURIComponent(id);
  }
  return null;
}
