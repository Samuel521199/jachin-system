/**
 * 配对 / 运维：为指定 tenant_id 批量授予「已审核且 PUBLIC」商品的 LICENSE。
 * 用于 L2 manifest 拉取：manifest 仅包含 user_licenses 中 ACTIVE 的 item。
 */
import { getDb } from "@/db";
import { pluginsRegistry, userLicenses } from "@/db/schema";
import { and, eq } from "drizzle-orm";

type NexusDb = NonNullable<ReturnType<typeof getDb>>;

export async function grantAllPublicApprovedForTenant(
  db: NexusDb,
  tenantId: string
): Promise<{ granted: number; skipped: number }> {
  const plugins = await db
    .select({ id: pluginsRegistry.id })
    .from(pluginsRegistry)
    .where(
      and(
        eq(pluginsRegistry.visibility, "PUBLIC"),
        eq(pluginsRegistry.status, "approved")
      )
    );

  let granted = 0;
  let skipped = 0;
  for (const { id } of plugins) {
    const existing = await db
      .select({ id: userLicenses.id })
      .from(userLicenses)
      .where(
        and(eq(userLicenses.tenantId, tenantId), eq(userLicenses.itemId, id))
      )
      .limit(1);
    if (existing.length > 0) {
      skipped++;
      continue;
    }
    await db.insert(userLicenses).values({
      tenantId,
      itemId: id,
      status: "ACTIVE",
    });
    granted++;
  }
  return { granted, skipped };
}
