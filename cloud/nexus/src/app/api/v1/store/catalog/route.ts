import { NextRequest, NextResponse } from "next/server";
import { getDb, isDatabaseConfigured } from "@/db";
import { pluginsRegistry } from "@/db/schema";
import { eq, and, desc, sql } from "drizzle-orm";

export const dynamic = "force-dynamic";

/** 允许的 item_type 筛选值，防止非法注入 */
const ALLOWED_ITEM_TYPES = ["SKILL", "MCP"] as const;

/**
 * GET /api/v1/store/catalog
 * 面向前端网页的商城货架
 *
 * 状态锁死：仅返回 status = 'approved' 的插件，未经审核的绝不展示。
 * 核心拦截：强制 visibility = 'PUBLIC'，绝不泄露 PRIVATE 私有技能。
 * 支持分页 (limit, offset)，支持按 item_type 筛选。
 */
export async function GET(request: NextRequest) {
  try {
    if (!isDatabaseConfigured()) {
      return NextResponse.json(
        {
          success: true,
          data: [],
          meta: { total: 0, limit: 20, offset: 0, source: "fallback" },
        },
        { status: 200 }
      );
    }

    const { searchParams } = new URL(request.url);
    const limitRaw = searchParams.get("limit");
    const offsetRaw = searchParams.get("offset");
    const itemTypeRaw = searchParams.get("item_type");

    // 分页参数：安全解析，防止注入与越界
    const limit = Math.min(Math.max(parseInt(limitRaw ?? "20", 10) || 20, 1), 100);
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

    const [rows, countResult] = await Promise.all([
      db
        .select({
          id: pluginsRegistry.id,
          itemType: pluginsRegistry.itemType,
          name: pluginsRegistry.name,
          description: pluginsRegistry.description,
          developerId: pluginsRegistry.developerId,
          priceMonthly: pluginsRegistry.priceMonthly,
          runtimeTier: pluginsRegistry.runtimeTier,
          requiredMcps: pluginsRegistry.requiredMcps,
          packageUrl: pluginsRegistry.packageUrl,
          createdAt: pluginsRegistry.createdAt,
        })
        .from(pluginsRegistry)
        .where(whereClause)
        .orderBy(desc(pluginsRegistry.createdAt))
        .limit(limit)
        .offset(offset),
      db
        .select({ count: sql<number>`count(*)::int` })
        .from(pluginsRegistry)
        .where(whereClause),
    ]);

    const total = countResult[0]?.count ?? 0;

    const data = rows.map((r) => ({
      id: r.id,
      item_type: r.itemType,
      name: r.name,
      description: r.description ?? null,
      developer_id: r.developerId ?? null,
      price_monthly: r.priceMonthly ? Number(r.priceMonthly) : 0,
      runtime_tier: r.runtimeTier,
      required_mcps: (r.requiredMcps as string[]) ?? [],
      package_url: r.packageUrl ?? null,
      created_at: r.createdAt?.toISOString() ?? null,
    }));

    return NextResponse.json({
      success: true,
      data,
      meta: { total, limit, offset },
    });
  } catch (e) {
    console.error("[store/catalog] Unexpected error:", e);
    return NextResponse.json(
      { success: false, error: "Internal server error" },
      { status: 500 }
    );
  }
}
