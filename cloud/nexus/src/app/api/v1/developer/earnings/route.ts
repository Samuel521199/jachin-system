import { NextRequest, NextResponse } from "next/server";
import { getDb, isDatabaseConfigured } from "@/db";
import { developerPayouts, pluginsRegistry } from "@/db/schema";
import { eq } from "drizzle-orm";
import { extractDeveloperId } from "@/lib/tenant";

export const dynamic = "force-dynamic";

/**
 * GET /api/v1/developer/earnings
 * 开发者收益查询 — 查看自己的 Skill/MCP 被调用次数与收益
 *
 * 鉴权：X-Developer-Id 或 Authorization: Bearer <JWT>（sub/developer_id）
 *
 * 返回：
 * - items: 按商品维度的调用次数、待结算金额、已结算金额
 * - summary: 汇总 total_calls, unpaid_amount_cents, paid_amount_cents
 */
export async function GET(request: NextRequest) {
  try {
    const developerId = extractDeveloperId(request);
    if (!developerId) {
      return NextResponse.json(
        {
          success: false,
          error: "UNAUTHORIZED",
          message:
            "Provide X-Developer-Id header or Authorization: Bearer <JWT> with developer_id/sub claim",
        },
        { status: 401 }
      );
    }

    if (!isDatabaseConfigured()) {
      return NextResponse.json({
        success: true,
        items: [],
        summary: {
          total_calls: 0,
          unpaid_amount_cents: 0,
          paid_amount_cents: 0,
        },
      });
    }

    const db = getDb()!;

    const rows = await db
      .select({
        itemId: developerPayouts.itemId,
        totalCalls: developerPayouts.totalCalls,
        unpaidAmountCents: developerPayouts.unpaidAmountCents,
        paidAmountCents: developerPayouts.paidAmountCents,
        lastUpdatedAt: developerPayouts.lastUpdatedAt,
      })
      .from(developerPayouts)
      .where(eq(developerPayouts.developerId, developerId));

    const pluginIds = [...new Set(rows.map((r) => r.itemId))];
    let nameMap: Record<string, string> = {};
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

    const items = rows.map((r) => ({
      item_id: r.itemId,
      name: nameMap[r.itemId] ?? r.itemId,
      total_calls: r.totalCalls,
      unpaid_amount_cents: r.unpaidAmountCents,
      paid_amount_cents: r.paidAmountCents,
      last_updated_at: r.lastUpdatedAt?.toISOString() ?? null,
    }));

    const summary = {
      total_calls: items.reduce((s, i) => s + i.total_calls, 0),
      unpaid_amount_cents: items.reduce((s, i) => s + i.unpaid_amount_cents, 0),
      paid_amount_cents: items.reduce((s, i) => s + i.paid_amount_cents, 0),
    };

    return NextResponse.json({
      success: true,
      items,
      summary,
    });
  } catch (e) {
    console.error("[developer/earnings] Error:", e);
    return NextResponse.json(
      {
        success: false,
        error: "INTERNAL_ERROR",
        message: "Failed to fetch earnings",
      },
      { status: 500 }
    );
  }
}
