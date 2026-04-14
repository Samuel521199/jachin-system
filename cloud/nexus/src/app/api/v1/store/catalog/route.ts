import { NextRequest, NextResponse } from "next/server";
import { getDb, isDatabaseConfigured } from "@/db";
import { pluginsRegistry } from "@/db/schema";
import { eq, and, desc, sql } from "drizzle-orm";
import { builtinToolsToCatalogRows } from "@/lib/builtin-l3-tools";
import { appendL1DebugLine } from "@/lib/l1-debug-file-log";

export const dynamic = "force-dynamic";

/** 允许的 item_type 筛选值，防止非法注入 */
const ALLOWED_ITEM_TYPES = ["SKILL", "MCP", "TOOL"] as const;

/**
 * GET /api/v1/store/catalog
 * 面向前端网页的商城货架
 *
 * 状态锁死：仅返回 status = 'approved' 的插件，未经审核的绝不展示。
 * 核心拦截：强制 visibility = 'PUBLIC'，绝不泄露 PRIVATE 私有技能。
 * 支持分页 (limit, offset)，支持按 item_type 筛选。
 */
export async function GET(request: NextRequest) {
  const reqUrl = request.url;
  try {
    if (!isDatabaseConfigured()) {
      appendL1DebugLine("store.catalog", {
        msg: "DATABASE_URL 未配置，返回空列表",
        url: reqUrl,
        has_database_url: Boolean((process.env.DATABASE_URL ?? "").trim()),
      });
      return NextResponse.json(
        {
          success: true,
          data: [],
          meta: {
            total: 0,
            limit: 20,
            offset: 0,
            source: "fallback",
            hint: "未检测到 DATABASE_URL：请在 cloud/nexus/.env.local 配置 Postgres 并重启 npm run dev。",
          },
        },
        { status: 200 }
      );
    }

    const { searchParams } = new URL(request.url);
    const limitRaw = searchParams.get("limit");
    const offsetRaw = searchParams.get("offset");
    const itemTypeRaw = searchParams.get("item_type");

    // 分页：TOOL 合并内置列表时单页可能较大，上限放宽到 500
    const limit = Math.min(Math.max(parseInt(limitRaw ?? "20", 10) || 20, 1), 500);
    const offset = Math.max(parseInt(offsetRaw ?? "0", 10) || 0, 0);

    // item_type 筛选：白名单校验
    const itemType =
      itemTypeRaw && ALLOWED_ITEM_TYPES.includes(itemTypeRaw as (typeof ALLOWED_ITEM_TYPES)[number])
        ? (itemTypeRaw as (typeof ALLOWED_ITEM_TYPES)[number])
        : null;

    const db = getDb()!;

    // 强制过滤：visibility = 'PUBLIC' 且 status = 'approved'，未经审核的插件绝对禁止展示
    const baseWhere = and(
      eq(pluginsRegistry.visibility, "PUBLIC"),
      eq(pluginsRegistry.status, "approved")
    );
    const whereClause = itemType
      ? and(baseWhere, eq(pluginsRegistry.itemType, itemType))
      : baseWhere;

    const selectCols = {
      id: pluginsRegistry.id,
      pluginId: pluginsRegistry.pluginId,
      itemType: pluginsRegistry.itemType,
      name: pluginsRegistry.name,
      description: pluginsRegistry.description,
      developerId: pluginsRegistry.developerId,
      priceMonthly: pluginsRegistry.priceMonthly,
      runtimeTier: pluginsRegistry.runtimeTier,
      requiredMcps: pluginsRegistry.requiredMcps,
      packageUrl: pluginsRegistry.packageUrl,
      createdAt: pluginsRegistry.createdAt,
    };

    const mapRow = (r: {
      id: string;
      pluginId: string;
      itemType: string;
      name: string;
      description: string | null;
      developerId: string | null;
      priceMonthly: number | null;
      runtimeTier: string;
      requiredMcps: unknown;
      packageUrl: string | null;
      createdAt: Date | null;
    }) => ({
      id: r.id,
      plugin_id: r.pluginId,
      item_type: r.itemType,
      name: r.name,
      description: r.description ?? null,
      developer_id: r.developerId ?? null,
      price_monthly: r.priceMonthly ? Number(r.priceMonthly) : 0,
      runtime_tier: r.runtimeTier,
      required_mcps: (r.requiredMcps as string[]) ?? [],
      package_url: r.packageUrl ?? null,
      created_at: r.createdAt?.toISOString() ?? null,
      runtime_builtin: false as const,
      tool_id: null as string | null,
    });

    // TOOL：先拉取**全部**已审核上架的 TOOL 行，再与内置列表合并后做 slice（禁止对 SQL 先 limit 再合并，否则会丢内置或截断错误）
    if (itemType === "TOOL") {
      const builtins = builtinToolsToCatalogRows();
      let dbTOOLRows: Parameters<typeof mapRow>[0][] = [];
      let dbTotal = 0;
      try {
        const [rows, countResult] = await Promise.all([
          db
            .select(selectCols)
            .from(pluginsRegistry)
            .where(whereClause)
            .orderBy(desc(pluginsRegistry.createdAt))
            .limit(5000),
          db.select({ count: sql<number>`count(*)::int` }).from(pluginsRegistry).where(whereClause),
        ]);
        dbTOOLRows = rows as Parameters<typeof mapRow>[0][];
        dbTotal = countResult[0]?.count ?? 0;
      } catch (dbErr) {
        /** 远端常见：未跑 drizzle/0015，`item_type` 枚举无 TOOL → SQL 失败；旧逻辑整段 500，前端原子工具 tab 空 */
        appendL1DebugLine("store.catalog", {
          msg: "tool_db_query_failed_fallback_builtins",
          error: dbErr instanceof Error ? dbErr.message : String(dbErr),
        });
        const merged = builtins.slice(offset, offset + limit);
        return NextResponse.json({
          success: true,
          data: merged,
          meta: {
            total: builtins.length,
            limit,
            offset,
            builtin_tools_count: builtins.length,
            db_count: 0,
            hint:
              "TOOL 数据库查询失败（常见：Postgres 未执行 drizzle/0015_item_type_tool.sql，item_type 枚举缺少 TOOL）。当前仅展示 L3 内置原子工具；执行迁移后刷新可合并数据库中的 TOOL 上架项。",
          },
        });
      }
      const dbRows = dbTOOLRows.map(mapRow);
      /** 与内置 util:get_weather_lite 同能力的上架行仅用于登记，避免货架双卡片（id 含连字符与下划线两种历史写法） */
      const dbRowsDeduped = dbRows.filter((r) => {
        const isWeatherStub =
          r.plugin_id === "com.jachin.tool.util_weather_lite" ||
          r.plugin_id === "com.jachin.tool.util-weather-lite";
        if (!isWeatherStub) return true;
        const hasBuiltinWeather = builtins.some((b) => b.tool_id === "util:get_weather_lite");
        return !hasBuiltinWeather;
      });
      const merged = [...builtins, ...dbRowsDeduped];
      const total = merged.length;
      const data = merged.slice(offset, offset + limit);
      appendL1DebugLine("store.catalog", {
        msg: "ok",
        item_type: "TOOL",
        builtin_count: builtins.length,
        db_approved_tool_count: dbTotal,
        merged_total: total,
        limit,
        offset,
        returned: data.length,
        log_path: "see JACHIN_L1_DEBUG_LOG or ~/.jachin/l1_debug.log",
      });
      return NextResponse.json({
        success: true,
        data,
        meta: {
          total,
          limit,
          offset,
          builtin_tools_count: builtins.length,
          db_count: dbTotal,
        },
      });
    }

    const [rows, countResult] = await Promise.all([
      db
        .select(selectCols)
        .from(pluginsRegistry)
        .where(whereClause)
        .orderBy(desc(pluginsRegistry.createdAt))
        .limit(limit)
        .offset(offset),
      db.select({ count: sql<number>`count(*)::int` }).from(pluginsRegistry).where(whereClause),
    ]);

    const dbTotal = countResult[0]?.count ?? 0;
    const dbRows = rows.map(mapRow);

    let pendingSameType = 0;
    if (dbTotal === 0 && itemType) {
      const pWhere = and(
        eq(pluginsRegistry.visibility, "PUBLIC"),
        eq(pluginsRegistry.status, "pending"),
        eq(pluginsRegistry.itemType, itemType)
      );
      const [pc] = await db.select({ c: sql<number>`count(*)::int` }).from(pluginsRegistry).where(pWhere);
      pendingSameType = Number(pc?.c ?? 0);
    }

    appendL1DebugLine("store.catalog", {
      msg: "ok",
      item_type: itemType ?? "all",
      db_approved_count: dbTotal,
      pending_same_type: pendingSameType,
      limit,
      offset,
      returned: dbRows.length,
    });

    const hints: string[] = [];
    if (dbTotal === 0 && itemType && pendingSameType > 0) {
      hints.push(
        `有 ${pendingSameType} 条 ${itemType} 处于 pending（待审核），商店仅展示 approved。请打开 /dashboard/admin/review 审核，或本机 .env.local 设 NEXUS_AUTO_APPROVE=1 后重启 Nexus。`
      );
    }

    return NextResponse.json({
      success: true,
      data: dbRows,
      meta: {
        total: dbTotal,
        limit,
        offset,
        ...(pendingSameType > 0 ? { pending_count: pendingSameType } : {}),
        ...(hints.length ? { hints } : {}),
      },
    });
  } catch (e) {
    console.error("[store/catalog] Unexpected error:", e);
    appendL1DebugLine("store.catalog", {
      msg: "exception",
      error: e instanceof Error ? e.message : String(e),
      stack: e instanceof Error ? e.stack : undefined,
    });
    return NextResponse.json(
      { success: false, error: "Internal server error" },
      { status: 500 }
    );
  }
}
