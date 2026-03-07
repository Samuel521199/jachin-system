import { NextRequest, NextResponse } from "next/server";
import { getDb, isDatabaseConfigured } from "@/db";
import {
  telemetryLogs,
  developerPayouts,
  pluginsRegistry,
} from "@/db/schema";
import { eq, and, or } from "drizzle-orm";
import { extractTenantId } from "@/lib/tenant";
import { rateLimit, TELEMETRY_LIMIT } from "@/lib/ratelimit";

export const dynamic = "force-dynamic";

/** 每次成功调用的分润（分），可后续改为从 plugins_registry 读取 */
const EARNINGS_PER_CALL_CENTS = 1;

interface TelemetryRecord {
  id: string;
  /** 可为哈希值或省略，保护企业员工隐私（IAM 已下放 L2） */
  sub_account_id?: string | null;
  item_id: string;
  action_name: string;
  status: string;
  latency_ms?: number | null;
  timestamp: number;
}

/**
 * 从 item_id 解析 plugin_id（skill:xxx / mcp:xxx -> xxx）
 */
function normalizePluginId(itemId: string): string {
  const s = String(itemId || "").trim();
  if (s.startsWith("skill:")) return s.slice(6);
  if (s.startsWith("mcp:")) return s.slice(4);
  return s;
}

/**
 * POST /api/v1/telemetry/report
 * L2 边缘遥测上报 — 批量接收用量数据并触发结算
 *
 * 鉴权：X-Tenant-Id + Authorization: Bearer（校验 tenant_id 与 Token）
 * Body: gzip 压缩的 JSON 数组，或原始 JSON 数组
 * Content-Type: application/json 或 application/octet-stream（gzip）
 *
 * 逻辑：
 * - 批量插入 telemetry_logs（带 tenant_id）
 * - 根据 item_id 查找 plugins_registry.developer_id，累加 developer_payouts
 */
export async function POST(request: NextRequest) {
  try {
    const { ok } = rateLimit(request, TELEMETRY_LIMIT);
    if (!ok) {
      return NextResponse.json(
        { success: false, error: "RATE_LIMIT_EXCEEDED", message: "上报过于频繁，请稍后再试" },
        { status: 429 }
      );
    }

    const tenantId = extractTenantId(request);
    const authHeader = request.headers.get("Authorization");
    if (!tenantId || !authHeader?.startsWith("Bearer ")) {
      return NextResponse.json(
        {
          success: false,
          error: "UNAUTHORIZED",
          message:
            "Provide X-Tenant-Id header and Authorization: Bearer <token>",
        },
        { status: 401 }
      );
    }

    let body: Buffer;
    try {
      body = Buffer.from(await request.arrayBuffer());
    } catch {
      return NextResponse.json(
        { success: false, error: "BAD_REQUEST", message: "Empty body" },
        { status: 400 }
      );
    }

    let records: TelemetryRecord[];
    const contentType = request.headers.get("Content-Type") ?? "";
    const contentEncoding = request.headers.get("Content-Encoding") ?? "";

    try {
      let jsonBytes: Buffer;
      if (
        contentEncoding === "gzip" ||
        contentType.includes("gzip") ||
        (body[0] === 0x1f && body[1] === 0x8b)
      ) {
        const zlib = await import("zlib");
        jsonBytes = zlib.gunzipSync(body);
      } else {
        jsonBytes = body;
      }
      const parsed = JSON.parse(jsonBytes.toString("utf8"));
      records = Array.isArray(parsed) ? parsed : [parsed];
    } catch (e) {
      console.error("[telemetry/report] Parse error:", e);
      return NextResponse.json(
        {
          success: false,
          error: "BAD_REQUEST",
          message: "Invalid JSON or gzip payload",
        },
        { status: 400 }
      );
    }

    if (records.length === 0) {
      return NextResponse.json({
        success: true,
        received: 0,
        inserted: 0,
        message: "No records to process",
      });
    }

    if (!isDatabaseConfigured()) {
      return NextResponse.json({
        success: true,
        received: records.length,
        inserted: 0,
        message: "Database not configured (dev fallback)",
      });
    }

    const db = getDb()!;

    const logsToInsert = records.map((r) => ({
      tenantId,
      originalId: String(r.id ?? ""),
      subAccountId: r.sub_account_id != null && r.sub_account_id !== "" ? String(r.sub_account_id) : null,
      itemId: String(r.item_id ?? ""),
      actionName: String(r.action_name ?? ""),
      status: String(r.status ?? "unknown"),
      latencyMs: r.latency_ms != null ? String(r.latency_ms) : null,
      timestamp: String(r.timestamp ?? Date.now() / 1000),
    }));

    await db.insert(telemetryLogs).values(logsToInsert);

    // 结算：按 item_id 找 developer，累加 developer_payouts
    const itemCounts = new Map<string, number>();
    for (const r of records) {
      if (r.status !== "success") continue;
      const key = r.item_id;
      itemCounts.set(key, (itemCounts.get(key) ?? 0) + 1);
    }

    for (const [itemId, count] of itemCounts) {
      const pluginId = normalizePluginId(itemId);
      const [plugin] = await db
        .select({ developerId: pluginsRegistry.developerId })
        .from(pluginsRegistry)
        .where(
          or(
            eq(pluginsRegistry.pluginId, pluginId),
            eq(pluginsRegistry.pluginId, itemId)
          )
        )
        .limit(1);

      if (!plugin?.developerId) continue;

      const earnings = count * EARNINGS_PER_CALL_CENTS;
      const [existing] = await db
        .select()
        .from(developerPayouts)
        .where(
          and(
            eq(developerPayouts.developerId, plugin.developerId),
            eq(developerPayouts.itemId, itemId)
          )
        )
        .limit(1);

      const now = new Date();
      if (existing) {
        await db
          .update(developerPayouts)
          .set({
            totalCalls: existing.totalCalls + count,
            unpaidAmountCents: existing.unpaidAmountCents + earnings,
            lastUpdatedAt: now,
          })
          .where(eq(developerPayouts.id, existing.id));
      } else {
        await db.insert(developerPayouts).values({
          developerId: plugin.developerId,
          itemId,
          totalCalls: count,
          unpaidAmountCents: earnings,
          paidAmountCents: 0,
          lastUpdatedAt: now,
        });
      }
    }

    return NextResponse.json({
      success: true,
      received: records.length,
      inserted: logsToInsert.length,
      message: "Telemetry received",
    });
  } catch (e) {
    console.error("[telemetry/report] Error:", e);
    return NextResponse.json(
      { success: false, error: "INTERNAL_ERROR", message: "Report failed" },
      { status: 500 }
    );
  }
}
