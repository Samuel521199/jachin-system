import { sql } from "drizzle-orm";
import type { AppDb } from "@/db";

const UUID_REGEX = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * 使用原生 SQL 查询 edge_agents，避免在下载站 schema 中重复整张表及 FK。
 */
export async function findActiveEdgeAgentByBearerToken(
  db: AppDb,
  token: string
): Promise<{ id: string } | null> {
  const isUuid = UUID_REGEX.test(token);

  const rows = isUuid
    ? await db.execute(sql`
        SELECT id::text AS id FROM edge_agents
        WHERE status = 'active'
          AND (auth_token = ${token} OR id::text = ${token})
        LIMIT 1
      `)
    : await db.execute(sql`
        SELECT id::text AS id FROM edge_agents
        WHERE status = 'active' AND auth_token = ${token}
        LIMIT 1
      `);

  const r = rows[0] as { id: string } | undefined;
  if (r?.id) return { id: r.id };

  if (isUuid) {
    const rows2 = await db.execute(sql`
      SELECT id::text AS id FROM edge_agents WHERE id::text = ${token} LIMIT 1
    `);
    const r2 = rows2[0] as { id: string } | undefined;
    if (r2?.id) return { id: r2.id };
  }

  const rows3 = await db.execute(sql`
    SELECT id::text AS id FROM edge_agents WHERE auth_token = ${token} LIMIT 1
  `);
  const r3 = rows3[0] as { id: string } | undefined;
  return r3?.id ? { id: r3.id } : null;
}
