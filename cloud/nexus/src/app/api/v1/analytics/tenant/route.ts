import { NextRequest, NextResponse } from "next/server";
import { getDb, isDatabaseConfigured } from "@/db";
import { sql } from "drizzle-orm";
import { extractTenantIdAllowingMachineFallback } from "@/lib/tenant";

export const dynamic = "force-dynamic";

/**
 * GET /api/v1/analytics/tenant?range=24h|7d
 * 企业主审计大屏 — 聚合 telemetry_logs 中当前 tenant 的用量数据
 *
 * 使用 idx_telemetry_logs_tenant_ts 索引，避免全表扫描
 */
export async function GET(request: NextRequest) {
  try {
    const tenantId = await extractTenantIdAllowingMachineFallback(request);
    if (!tenantId) {
      return NextResponse.json(
        { success: false, error: "Missing tenant_id" },
        { status: 401 }
      );
    }

    const range = request.nextUrl.searchParams.get("range") || "24h";
    const nowSec = Date.now() / 1000;
    const windowSec = range === "7d" ? 7 * 24 * 3600 : 24 * 3600;
    const bucketSec = range === "7d" ? 24 * 3600 : 3600;
    const bucketLabel = range === "7d" ? "day" : "hour";

    if (!isDatabaseConfigured()) {
      return NextResponse.json({
        success: true,
        usageTrend: [],
        skillRanking: [],
        globalStats: { successRate: 100, avgLatencyMs: 0 },
        meta: { tenant_id: tenantId, range },
      });
    }

    const db = getDb()!;
    const cutoff = nowSec - windowSec;

    // 1. 用量趋势：按时间桶聚合（使用 idx_telemetry_logs_tenant_ts）
    // GROUP BY 1 避免 PostgreSQL 对重复表达式的严格校验（42803）
    const trendRes = await db.execute(
      sql`SELECT floor((timestamp::numeric) / ${bucketSec}) * ${bucketSec} AS bucket, count(*)::int AS cnt
          FROM telemetry_logs
          WHERE tenant_id = ${tenantId} AND timestamp::numeric >= ${cutoff}
          GROUP BY 1
          ORDER BY 1 ASC`
    );
    const trendRows = Array.isArray(trendRes) ? trendRes : (trendRes as { rows?: unknown[] }).rows ?? [];
    const usageTrend = (trendRows as { bucket: number; cnt: number }[]).map(
      (r) => ({
        [bucketLabel]: r.bucket,
        calls: r.cnt,
        label:
          bucketLabel === "hour"
            ? new Date(r.bucket * 1000).toLocaleTimeString("zh-CN", {
                hour: "2-digit",
                minute: "2-digit",
              })
            : new Date(r.bucket * 1000).toLocaleDateString("zh-CN", {
                month: "short",
                day: "numeric",
              }),
      })
    );

    // 2. 活跃技能排名：按 item_id 聚合
    const skillRes = await db.execute(
      sql`SELECT item_id, count(*)::int AS cnt
          FROM telemetry_logs
          WHERE tenant_id = ${tenantId} AND timestamp::numeric >= ${cutoff}
          GROUP BY item_id
          ORDER BY cnt DESC
          LIMIT 15`
    );
    const skillRows = Array.isArray(skillRes) ? skillRes : (skillRes as { rows?: unknown[] }).rows ?? [];
    const skillRanking = (skillRows as { item_id: string; cnt: number }[]).map(
      (r) => ({
        item_id: r.item_id,
        name: r.item_id.replace(/^(skill|mcp):/, "").trim() || r.item_id,
        calls: r.cnt,
      })
    );

    // 3. 全局调用成功率与平均耗时（完全匿名，不涉及 sub_account_id）
    const statsRes = await db.execute(
      sql`SELECT
          count(*)::int AS total,
          count(*) FILTER (WHERE status = 'success')::int AS success_cnt,
          coalesce(avg(latency_ms::numeric), 0)::float AS avg_latency
        FROM telemetry_logs
        WHERE tenant_id = ${tenantId} AND timestamp::numeric >= ${cutoff}`
    );
    const statsRows = Array.isArray(statsRes) ? statsRes : (statsRes as { rows?: unknown[] }).rows ?? [];
    const stats = (statsRows[0] as { total: number; success_cnt: number; avg_latency: number }) ?? {
      total: 0,
      success_cnt: 0,
      avg_latency: 0,
    };
    const globalStats = {
      successRate: stats.total > 0 ? Math.round((stats.success_cnt / stats.total) * 1000) / 10 : 100,
      avgLatencyMs: Math.round(stats.avg_latency * 10) / 10,
    };

    return NextResponse.json({
      success: true,
      usageTrend,
      skillRanking,
      globalStats,
      meta: { tenant_id: tenantId, range },
    });
  } catch (e) {
    console.error("[analytics/tenant] Error:", e);
    return NextResponse.json(
      { success: false, error: "Internal error" },
      { status: 500 }
    );
  }
}
