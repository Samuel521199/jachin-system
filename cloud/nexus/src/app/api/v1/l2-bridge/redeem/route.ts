import { NextRequest, NextResponse } from "next/server";
import { randomUUID } from "crypto";
import { getDb, isDatabaseConfigured } from "@/db";
import { error as logError } from "@/lib/console-utc";
import { edgeAgents, users } from "@/db/schema";
import { userCanManageL2Gateway } from "@/lib/l1-workspace-context";
import { l2BridgeStoreTake } from "@/lib/l2-bridge-store";
import { generatePairingShortCode } from "@/lib/pairing-code-util";

export const dynamic = "force-dynamic";

const DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000001";

function nexusBaseUrl(): string {
  return (
    process.env.NEXUS_PUBLIC_URL ||
    (process.env.NEXT_PUBLIC_VERCEL_URL
      ? `https://${process.env.NEXT_PUBLIC_VERCEL_URL}`
      : "http://localhost:3000")
  ).replace(/\/$/, "");
}

/**
 * POST /api/v1/l2-bridge/redeem
 * Body: { bridge_code }
 * 消费 mint 生成的一次性码，创建（或演示模式下模拟）edge_agents，返回与 pairing/status success 同形 JSON。
 */
export async function POST(req: NextRequest) {
  try {
    const body = await req.json().catch(() => ({}));
    const bridgeCode =
      typeof body.bridge_code === "string" ? body.bridge_code.trim() : "";
    if (!bridgeCode) {
      return NextResponse.json(
        { error: "missing_bridge_code" },
        { status: 400 }
      );
    }

    const entry = l2BridgeStoreTake(bridgeCode);
    if (!entry) {
      return NextResponse.json(
        { error: "invalid_or_expired", message: "绑定码无效或已过期" },
        { status: 404 }
      );
    }

    const baseUrl = nexusBaseUrl();

    if (!isDatabaseConfigured()) {
      const instanceId = `jachin-web-${randomUUID().slice(0, 8)}`;
      return NextResponse.json({
        status: "success",
        access_token: `jch-bridge-${randomUUID().replace(/-/g, "")}`,
        layer1_public_key: null,
        instance_id: instanceId,
        l1_user_id: entry.user_id,
        tenant_id: entry.organization_id,
        nexus_base_url: baseUrl,
      });
    }

    const db = getDb()!;

    if (
      entry.user_id &&
      entry.user_id !== DEFAULT_USER_ID &&
      entry.organization_id
    ) {
      const ok = await userCanManageL2Gateway(
        db,
        entry.user_id,
        entry.organization_id
      );
      if (!ok) {
        return NextResponse.json(
          {
            error: "GATEWAY_FORBIDDEN",
            message:
              "权限不足：仅工作区所有者或管理员可完成 L2 网页桥接绑定。",
          },
          { status: 403 }
        );
      }
    }

    if (entry.user_id === DEFAULT_USER_ID || !entry.user_id) {
      await db
        .insert(users)
        .values({
          id: DEFAULT_USER_ID,
          name: "默认用户",
        })
        .onConflictDoNothing({ target: users.id });
    }

    const authToken = `jch-${randomUUID().replace(/-/g, "")}`;
    let inserted = false;
    let agentId = "";
    for (let retry = 0; retry < 5 && !inserted; retry++) {
      const shortCode = generatePairingShortCode();
      try {
        const [row] = await db
          .insert(edgeAgents)
          .values({
            pairingCode: shortCode,
            status: "active",
            name: `L2-Web-${shortCode}`,
            userId: entry.user_id,
            organizationId: entry.organization_id ?? undefined,
            authToken,
            pairingExpiresAt: null,
          })
          .returning({ id: edgeAgents.id });
        agentId = row?.id ?? "";
        inserted = true;
      } catch (e: unknown) {
        const err = e as { code?: string };
        if (err?.code === "23505") continue;
        logError("[l2-bridge/redeem] DB insert error:", e);
        return NextResponse.json(
          { error: "redeem_failed", message: "创建边缘实例失败" },
          { status: 500 }
        );
      }
    }

    if (!inserted || !agentId) {
      return NextResponse.json(
        { error: "redeem_failed", message: "无法分配唯一配对码，请重试" },
        { status: 500 }
      );
    }

    return NextResponse.json({
      status: "success",
      access_token: authToken,
      layer1_public_key: null,
      instance_id: agentId,
      l1_user_id: entry.user_id,
      tenant_id: entry.organization_id,
      nexus_base_url: baseUrl,
    });
  } catch (e) {
    logError("[l2-bridge/redeem] Error:", e);
    return NextResponse.json(
      { error: "redeem_failed", message: "兑换失败" },
      { status: 500 }
    );
  }
}
