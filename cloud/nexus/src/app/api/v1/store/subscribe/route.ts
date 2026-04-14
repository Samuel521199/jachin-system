import { NextRequest, NextResponse } from "next/server";
import { getDb, isDatabaseConfigured } from "@/db";
import { pluginsRegistry, userLicenses } from "@/db/schema";
import { eq, and } from "drizzle-orm";
import { extractTenantIdAllowingMachineFallback } from "@/lib/tenant";
import { isBuiltinToolCatalogId } from "@/lib/builtin-l3-tools";
import { appendL1DebugLine } from "@/lib/l1-debug-file-log";

export const dynamic = "force-dynamic";

/** UUID 格式校验 */
const UUID_REGEX = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * POST /api/v1/store/subscribe
 * 一键订阅接口 — 0 元购授权
 *
 * Body: { item_id: "uuid" }
 * 鉴权：与 sync/manifest 一致，提取 tenant_id
 * 逻辑：已订阅且 ACTIVE → 400；未订阅 → INSERT user_licenses
 */
export async function POST(request: NextRequest) {
  try {
    const tenantId = await extractTenantIdAllowingMachineFallback(request);
    if (!tenantId) {
      return NextResponse.json(
        {
          success: false,
          error: "未登录或缺少租户标识",
          code: "UNAUTHORIZED",
          message: "请先登录（会话 JWT）或提供 X-Tenant-Id / Bearer / nexus_tenant_id cookie",
        },
        { status: 401 }
      );
    }

    let body: { item_id?: string };
    try {
      body = (await request.json()) as { item_id?: string };
    } catch {
      return NextResponse.json(
        {
          success: false,
          error: "请求体必须是有效的 JSON",
          code: "INVALID_JSON",
        },
        { status: 400 }
      );
    }

    const itemId = body.item_id?.trim();
    if (!itemId) {
      return NextResponse.json(
        {
          success: false,
          error: "缺少 item_id",
          code: "MISSING_ITEM_ID",
        },
        { status: 400 }
      );
    }

    if (!UUID_REGEX.test(itemId)) {
      return NextResponse.json(
        {
          success: false,
          error: "item_id 格式无效，应为 UUID",
          code: "INVALID_ITEM_ID",
        },
        { status: 400 }
      );
    }

    // L3 内置 Native 工具（商城展示用稳定 UUID，非可购 SKU）
    if (isBuiltinToolCatalogId(itemId)) {
      appendL1DebugLine("store.subscribe", { msg: "builtin_noop", item_id: itemId });
      return NextResponse.json({
        success: true,
        message: "此为 L3 运行时内置工具，无需订阅",
        runtime_builtin: true,
      });
    }

    if (!isDatabaseConfigured()) {
      return NextResponse.json(
        {
          success: false,
          error: "数据库未配置",
          code: "DB_NOT_CONFIGURED",
        },
        { status: 503 }
      );
    }

    const db = getDb()!;

    // 校验商品存在且可订阅（visibility = PUBLIC 且 status = approved）
    const [plugin] = await db
      .select({ id: pluginsRegistry.id })
      .from(pluginsRegistry)
      .where(
        and(
          eq(pluginsRegistry.id, itemId),
          eq(pluginsRegistry.visibility, "PUBLIC"),
          eq(pluginsRegistry.status, "approved")
        )
      )
      .limit(1);

    if (!plugin) {
      return NextResponse.json(
        {
          success: false,
          error: "商品不存在或未通过审核",
          code: "ITEM_NOT_FOUND",
        },
        { status: 404 }
      );
    }

    // 检查是否已订阅
    const [existing] = await db
      .select({ id: userLicenses.id, status: userLicenses.status })
      .from(userLicenses)
      .where(
        and(
          eq(userLicenses.tenantId, tenantId),
          eq(userLicenses.itemId, itemId)
        )
      )
      .limit(1);

    if (existing && existing.status === "ACTIVE") {
      return NextResponse.json(
        {
          success: false,
          error: "您已拥有该物资",
          code: "ALREADY_OWNED",
        },
        { status: 400 }
      );
    }

    // 未订阅：INSERT（0 元购）
    if (!existing) {
      await db.insert(userLicenses).values({
        tenantId,
        itemId,
        status: "ACTIVE",
        purchasedAt: new Date(),
      });
    } else {
      // 已存在但非 ACTIVE（如 EXPIRED），更新为 ACTIVE
      await db
        .update(userLicenses)
        .set({ status: "ACTIVE", purchasedAt: new Date() })
        .where(eq(userLicenses.id, existing.id));
    }

    appendL1DebugLine("store.subscribe", {
      msg: "subscribed",
      item_id: itemId,
      tenant_id: tenantId,
    });
    return NextResponse.json({
      success: true,
      message: "订阅成功",
      data: {
        item_id: itemId,
        tenant_id: tenantId,
        status: "ACTIVE",
      },
    });
  } catch (e) {
    console.error("[store/subscribe] Unexpected error:", e);
    return NextResponse.json(
      {
        success: false,
        error: "Internal server error",
        code: "INTERNAL_ERROR",
      },
      { status: 500 }
    );
  }
}
