import { NextRequest, NextResponse } from "next/server";
import { getDb, isDatabaseConfigured } from "@/db";
import { userLicenses } from "@/db/schema";
import { eq, and, or, gt, isNull } from "drizzle-orm";
import { extractTenantId } from "@/lib/tenant";

export const dynamic = "force-dynamic";

/**
 * GET /api/v1/store/licenses
 * 获取当前租户已订阅的 item_id 列表（ACTIVE 且未过期）
 *
 * 鉴权：与 sync/manifest 一致
 */
export async function GET(request: NextRequest) {
  try {
    const tenantId = extractTenantId(request);
    if (!tenantId) {
      return NextResponse.json(
        { success: true, data: [], meta: { tenant_id: null } },
        { status: 200 }
      );
    }

    if (!isDatabaseConfigured()) {
      return NextResponse.json(
        { success: true, data: [], meta: { tenant_id: tenantId } },
        { status: 200 }
      );
    }

    const db = getDb()!;
    const now = new Date();

    const rows = await db
      .select({ itemId: userLicenses.itemId })
      .from(userLicenses)
      .where(
        and(
          eq(userLicenses.tenantId, tenantId),
          eq(userLicenses.status, "ACTIVE"),
          or(
            isNull(userLicenses.expiresAt),
            gt(userLicenses.expiresAt, now)
          )
        )
      );

    const itemIds = rows.map((r) => r.itemId).filter(Boolean) as string[];

    return NextResponse.json({
      success: true,
      data: itemIds,
      meta: { tenant_id: tenantId, total: itemIds.length },
    });
  } catch (e) {
    console.error("[store/licenses] Unexpected error:", e);
    return NextResponse.json(
      { success: false, error: "Internal server error" },
      { status: 500 }
    );
  }
}
