import { NextRequest, NextResponse } from "next/server";
import { getDb, isDatabaseConfigured } from "@/db";
import { developerPayouts, pluginsRegistry } from "@/db/schema";
import { eq, sql } from "drizzle-orm";
import { extractDeveloperId } from "@/lib/tenant";

export const dynamic = "force-dynamic";

/**
 * GET /api/v1/analytics/developer
 * 开发者收益中心 — 总调用量、待结算、应用列表（成功率、平均耗时）
 *
 * 数据来源：developer_payouts + plugins_registry + telemetry_logs
 */
export async function GET(request: NextRequest) {
  try {
    const developerId = extractDeveloperId(request);
    if (!developerId) {
      return NextResponse.json(
        { success: false, error: "Missing developer_id" },
        { status: 401 }
      );
    }

    if (!isDatabaseConfigured()) {
      return NextResponse.json({
        success: true,
        totalCalls: 0,
        unpaidAmountCents: 0,
        paidAmountCents: 0,
        appList: [],
      });
    }

    const db = getDb()!;

    // 1. 从 developer_payouts 获取汇总与应用列表
    const payouts = await db
      .select({
        itemId: developerPayouts.itemId,
        totalCalls: developerPayouts.totalCalls,
        unpaidAmountCents: developerPayouts.unpaidAmountCents,
        paidAmountCents: developerPayouts.paidAmountCents,
      })
      .from(developerPayouts)
      .where(eq(developerPayouts.developerId, developerId));

    const totalCalls = payouts.reduce((s, p) => s + p.totalCalls, 0);
    const unpaidAmountCents = payouts.reduce((s, p) => s + p.unpaidAmountCents, 0);
    const paidAmountCents = payouts.reduce((s, p) => s + p.paidAmountCents, 0);

    // 2. 获取插件名称
    const pluginIds = [...new Set(payouts.map((p) => p.itemId))];
    const nameMap: Record<string, string> = {};
    if (pluginIds.length > 0) {
      const plugins = await db
        .select({
          pluginId: pluginsRegistry.pluginId,
          name: pluginsRegistry.name,
        })
        .from(pluginsRegistry)
        .where(eq(pluginsRegistry.developerId, developerId));
      for (const p of plugins) {
        nameMap[p.pluginId] = p.name ?? p.pluginId;
        nameMap[`skill:${p.pluginId}`] = p.name ?? p.pluginId;
        nameMap[`mcp:${p.pluginId}`] = p.name ?? p.pluginId;
      }
    }

    // 3. 从 telemetry_logs 聚合成功率与平均耗时（按 item_id）
    const appList: Array<{
      item_id: string;
      name: string;
      total_calls: number;
      unpaid_amount_cents: number;
      success_rate: number;
      avg_latency_ms: number;
    }> = [];

    for (const p of payouts) {
      const perfRes = await db.execute(
        sql`SELECT
            count(*)::int AS total,
            count(*) FILTER (WHERE status = 'success')::int AS success_cnt,
            coalesce(avg(latency_ms::float), 0)::float AS avg_latency
          FROM telemetry_logs
          WHERE item_id = ${p.itemId}`
      );
      const perfRows = Array.isArray(perfRes)
        ? perfRes
        : (perfRes as { rows?: unknown[] }).rows ?? [];
      const perf = (perfRows[0] as {
        total: number;
        success_cnt: number;
        avg_latency: number;
      }) ?? { total: 0, success_cnt: 0, avg_latency: 0 };

      const successRate =
        perf.total > 0 ? (perf.success_cnt / perf.total) * 100 : 100;

      appList.push({
        item_id: p.itemId,
        name: nameMap[p.itemId] ?? p.itemId.replace(/^(skill|mcp):/, ""),
        total_calls: p.totalCalls,
        unpaid_amount_cents: p.unpaidAmountCents,
        success_rate: Math.round(successRate * 10) / 10,
        avg_latency_ms: Math.round(perf.avg_latency * 10) / 10,
      });
    }

    return NextResponse.json({
      success: true,
      totalCalls,
      unpaidAmountCents,
      paidAmountCents,
      appList,
    });
  } catch (e) {
    console.error("[analytics/developer] Error:", e);
    return NextResponse.json(
      { success: false, error: "Internal error" },
      { status: 500 }
    );
  }
}
