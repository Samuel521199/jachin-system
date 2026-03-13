import { NextRequest, NextResponse } from "next/server";
import { randomUUID } from "crypto";
import { getDb, isDatabaseConfigured } from "@/db";
import { error as logError } from "@/lib/console-utc";
import { edgeAgents, users } from "@/db/schema";
import { pairingStoreGetByCode, pairingStoreApproveByCode } from "@/lib/pairing-store";
import { eq } from "drizzle-orm";

const DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000001";

/**
 * POST /api/v1/pairing/confirm
 * 阶段 2：用户在 Web Console 输入 6 位码，授权绑定
 * Drizzle ORM：更新 edge_agents 为 active
 */
export async function POST(req: NextRequest) {
  try {
    const body = await req.json().catch(() => ({}));
    const { code, user_id: bodyUserId } = body;

    if (!code || typeof code !== "string") {
      return NextResponse.json(
        { success: false, error: "Missing or invalid code" },
        { status: 400 }
      );
    }

    const shortCode = String(code).trim().toUpperCase().replace(/[-_\s]/g, "");

    if (!isDatabaseConfigured()) {
      const session = pairingStoreGetByCode(shortCode);
      if (!session) {
        return NextResponse.json(
          { success: false, error: "配对码无效或已过期" },
          { status: 404 }
        );
      }
      if (new Date(session.expires_at) < new Date()) {
        return NextResponse.json(
          { success: false, error: "配对码已过期" },
          { status: 410 }
        );
      }
      if (session.status === "approved") {
        return NextResponse.json({
          success: true,
          instance_id: session.instance_id ?? "dev-layer2-001",
          l1_user_id: session.user_id ?? null,
          message: "Edge Agent successfully paired!",
        });
      }
      const instanceId = `jachin-${randomUUID().slice(0, 8)}`;
      const memUserId = bodyUserId ?? null;
      pairingStoreApproveByCode(shortCode, instanceId, memUserId ?? undefined);
      return NextResponse.json({
        success: true,
        instance_id: instanceId,
        l1_user_id: memUserId,
        message: "Edge Agent successfully paired!",
      });
    }

    const db = getDb()!;
    let userId: string | null = bodyUserId ?? null;
    if (userId === DEFAULT_USER_ID || !userId) {
      // 确保默认用户存在，否则外键约束会失败
      await db
        .insert(users)
        .values({
          id: DEFAULT_USER_ID,
          name: "默认用户",
        })
        .onConflictDoNothing({ target: users.id });
      userId = DEFAULT_USER_ID;
    }

    const [agent] = await db
      .select({ id: edgeAgents.id, status: edgeAgents.status, pairingExpiresAt: edgeAgents.pairingExpiresAt })
      .from(edgeAgents)
      .where(eq(edgeAgents.pairingCode, shortCode))
      .limit(1);

    if (!agent) {
      return NextResponse.json(
        { success: false, error: "配对码无效或已过期" },
        { status: 404 }
      );
    }

    if (agent.status === "active") {
      const [activeAgent] = await db
        .select({ userId: edgeAgents.userId })
        .from(edgeAgents)
        .where(eq(edgeAgents.id, agent.id))
        .limit(1);
      return NextResponse.json({
        success: true,
        instance_id: agent.id,
        l1_user_id: activeAgent?.userId ?? null,
        message: "Edge Agent successfully paired!",
      });
    }

    if (agent.pairingExpiresAt && new Date(agent.pairingExpiresAt) < new Date()) {
      await db
        .update(edgeAgents)
        .set({ status: "offline" })
        .where(eq(edgeAgents.id, agent.id));
      return NextResponse.json(
        { success: false, error: "配对码已过期" },
        { status: 410 }
      );
    }

    const authToken = `jch-${randomUUID().replace(/-/g, "")}`;

    await db
      .update(edgeAgents)
      .set({
        status: "active",
        userId,
        authToken,
        pairingExpiresAt: null,
      })
      .where(eq(edgeAgents.id, agent.id));

    return NextResponse.json({
      success: true,
      instance_id: agent.id,
      l1_user_id: userId,
      message: "Edge Agent successfully paired!",
    });
  } catch (e) {
    logError("[pairing/confirm] Error:", e);
    return NextResponse.json(
      { success: false, error: "配对确认失败" },
      { status: 500 }
    );
  }
}
