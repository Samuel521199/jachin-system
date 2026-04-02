/**
 * L2 配对 access_token（edge_agents.auth_token 或回退为 agent.id）解析为 manifest 用租户 UUID。
 * 优先使用 edge_agents.organization_id；缺失时按 user_id 解析其默认工作区（不自动创建组织）。
 */
import { and, eq, or } from "drizzle-orm";
import type { NextRequest } from "next/server";
import type { NexusDb } from "@/lib/tenant";
import { isTenantUuidString } from "@/lib/tenant";
import { edgeAgents } from "@/db/schema";
import { resolveTenantIdForEdgeUser } from "@/lib/l1-workspace-context";

export function extractBearerTokenRaw(request: NextRequest): string | null {
  const authHeader = request.headers.get("Authorization");
  if (!authHeader?.startsWith("Bearer ")) return null;
  const token = authHeader.slice(7).trim();
  return token.length > 0 ? token : null;
}

/** 边缘 Bearer 解析后的行上下文（P3：配合 X-Tenant-Id 多工作区 manifest） */
export async function resolveEdgeAgentManifestContext(
  db: NexusDb,
  bearer: string
): Promise<{ userId: string | null; organizationId: string | null } | null> {
  const tokenOrId = isTenantUuidString(bearer)
    ? or(eq(edgeAgents.authToken, bearer), eq(edgeAgents.id, bearer))
    : eq(edgeAgents.authToken, bearer);

  const [agent] = await db
    .select({
      organizationId: edgeAgents.organizationId,
      userId: edgeAgents.userId,
    })
    .from(edgeAgents)
    .where(and(eq(edgeAgents.status, "active"), tokenOrId))
    .limit(1);

  if (!agent) return null;
  return {
    userId: agent.userId,
    organizationId: agent.organizationId,
  };
}

/**
 * Bearer 为 L1 下发的边缘凭证时，返回 organizations.id；否则 null。
 */
export async function resolveTenantIdFromEdgeAgentBearer(
  db: NexusDb,
  bearer: string
): Promise<string | null> {
  const ctx = await resolveEdgeAgentManifestContext(db, bearer);
  if (!ctx) return null;

  if (ctx.organizationId) {
    return ctx.organizationId;
  }
  if (ctx.userId) {
    return await resolveTenantIdForEdgeUser(db, ctx.userId);
  }
  return null;
}
