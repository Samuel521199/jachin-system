/**
 * tenant_id 提取骨架
 * 面向 L2 边缘网关的「神谕同步」接口鉴权
 *
 * 提取优先级：
 * 1. X-Tenant-Id 请求头（API Key / 服务令牌场景）
 * 2. Authorization: Bearer <JWT> 中解码 tenant_id（sub 或 tenant_id claim）
 * 3. Cookie nexus_tenant_id（同源 Web 场景，演示模式）
 *
 * 后续接入 Auth.js / JWT 时在此扩展。
 */
import { NextRequest } from "next/server";

export interface TenantExtractResult {
  tenantId: string;
  source: "header" | "jwt";
}

/**
 * 从请求中提取 tenant_id
 *
 * 伪代码 / 骨架：
 * - 若存在 X-Tenant-Id 且非空，直接使用
 * - 若存在 Authorization: Bearer <token>，尝试解析为 JWT 并取 sub/tenant_id
 * - 未配置 JWT 解析时，可在此处接入 jose 或 jsonwebtoken 解码
 *
 * @example 接入 JWT 解码（需安装 jose）:
 *   import * as jose from "jose";
 *   const { payload } = await jose.jwtVerify(token, publicKey);
 *   return payload.tenant_id ?? payload.sub ?? null;
 */
export function extractTenantId(request: NextRequest): string | null {
  // 1. 优先从 X-Tenant-Id 请求头获取（L2 网关常用）
  const headerTenant = request.headers.get("X-Tenant-Id")?.trim();
  if (headerTenant && headerTenant.length > 0) {
    return headerTenant;
  }

  // 2. 从 Authorization: Bearer <token> 提取
  const authHeader = request.headers.get("Authorization");
  if (authHeader?.startsWith("Bearer ")) {
    const token = authHeader.slice(7).trim();
    if (token) {
      try {
        const parts = token.split(".");
        if (parts.length === 3) {
          const payloadBase64 = parts[1];
          if (payloadBase64) {
            const payloadJson = Buffer.from(payloadBase64, "base64url").toString("utf8");
            const payload = JSON.parse(payloadJson) as Record<string, unknown>;
            const tenantId = (payload.tenant_id as string) ?? (payload.sub as string);
            if (typeof tenantId === "string" && tenantId.length > 0) {
              return tenantId;
            }
          }
        }
      } catch {
        /* ignore */
      }
    }
  }

  // 3. Cookie nexus_tenant_id（同源 Web 演示）
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
        if (parts.length === 3) {
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

  // Cookie nexus_developer_id（同源 Web 演示）
  const cookieHeader = request.headers.get("Cookie");
  if (cookieHeader) {
    const match = cookieHeader.match(/nexus_developer_id=([^;]+)/);
    const id = match?.[1]?.trim();
    if (id && id.length > 0) return decodeURIComponent(id);
  }
  return null;
}
