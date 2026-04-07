/**
 * 与 edge/heartbeat 一致：校验 L2 写入的 access_token（edge_agents.auth_token 或 agent id UUID）。
 */
import { and, eq, or } from "drizzle-orm";
import type { NexusDb } from "@/lib/tenant";
import { edgeAgents } from "@/db/schema";

const UUID_REGEX = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function findActiveEdgeAgentByBearerToken(
  db: NexusDb,
  token: string
): Promise<{ id: string } | null> {
  const isUuid = UUID_REGEX.test(token);
  const whereClause = isUuid
    ? or(
        and(eq(edgeAgents.authToken, token), eq(edgeAgents.status, "active")),
        and(eq(edgeAgents.id, token), eq(edgeAgents.status, "active"))
      )
    : and(eq(edgeAgents.authToken, token), eq(edgeAgents.status, "active"));

  const [agent] = await db
    .select({ id: edgeAgents.id })
    .from(edgeAgents)
    .where(whereClause)
    .limit(1);

  if (agent) return agent;

  if (isUuid) {
    const [byId] = await db
      .select({ id: edgeAgents.id })
      .from(edgeAgents)
      .where(eq(edgeAgents.id, token))
      .limit(1);
    if (byId) return byId;
  }

  const [byToken] = await db
    .select({ id: edgeAgents.id })
    .from(edgeAgents)
    .where(eq(edgeAgents.authToken, token))
    .limit(1);
  return byToken ?? null;
}
