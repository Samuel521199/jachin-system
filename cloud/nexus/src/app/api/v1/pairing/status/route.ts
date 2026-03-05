import { NextRequest, NextResponse } from "next/server";
import { getDb, isDatabaseConfigured } from "@/db";
import { edgeAgents } from "@/db/schema";
import { pairingStoreGetBySession, pairingStoreGetByCode } from "@/lib/pairing-store";
import { eq } from "drizzle-orm";

export const dynamic = "force-dynamic";

/**
 * GET /api/v1/pairing/status?code=XXX 或 ?session_id=XXX
 * 阶段 3：Layer 2 轮询配对状态
 * Drizzle ORM：查询 edge_agents，status=active 时返回 auth_token
 */
export async function GET(req: NextRequest) {
  try {
    const { searchParams } = new URL(req.url);
    const code = searchParams.get("code");
    const sessionId = searchParams.get("session_id");

    if (!code && !sessionId) {
      return NextResponse.json(
        { error: "Missing code or session_id" },
        { status: 400 }
      );
    }

    if (!isDatabaseConfigured()) {
      const session = code
        ? pairingStoreGetByCode(code)
        : pairingStoreGetBySession(sessionId ?? "");
      if (!session) {
        return NextResponse.json(
          { status: "expired", error: "会话不存在或已失效" },
          { status: 404 }
        );
      }
      if (new Date(session.expires_at) < new Date()) {
        return NextResponse.json({
          status: "expired",
          error: "配对码已过期",
        });
      }
      if (session.status !== "approved") {
        return NextResponse.json({
          status: "pending",
          message: "Waiting for Web confirmation...",
        });
      }
      const baseUrl =
        process.env.NEXUS_PUBLIC_URL ||
        (process.env.NEXT_PUBLIC_VERCEL_URL
          ? `https://${process.env.NEXT_PUBLIC_VERCEL_URL}`
          : "http://localhost:3000");
      return NextResponse.json({
        status: "success",
        access_token: `jch-mock-${(session?.session_id ?? sessionId ?? "").slice(0, 8)}`,
        layer1_public_key: null,
        instance_id: session.instance_id ?? "dev-layer2-001",
        nexus_base_url: baseUrl,
      });
    }

    const db = getDb()!;
    const normalizedCode = code ? String(code).trim().toUpperCase().replace(/-/g, "") : null;

    const [agent] = await db
      .select({
        id: edgeAgents.id,
        status: edgeAgents.status,
        authToken: edgeAgents.authToken,
        pairingExpiresAt: edgeAgents.pairingExpiresAt,
      })
      .from(edgeAgents)
      .where(
        normalizedCode
          ? eq(edgeAgents.pairingCode, normalizedCode)
          : eq(edgeAgents.id, sessionId!)
      )
      .limit(1);

    if (!agent) {
      return NextResponse.json(
        { status: "expired", error: "会话不存在或已失效" },
        { status: 404 }
      );
    }

    if (agent.pairingExpiresAt && new Date(agent.pairingExpiresAt) < new Date()) {
      await db
        .update(edgeAgents)
        .set({ status: "offline" })
        .where(eq(edgeAgents.id, agent.id));
      return NextResponse.json({
        status: "expired",
        error: "配对码已过期",
      });
    }

    if (agent.status !== "active") {
      return NextResponse.json({
        status: "pending",
        message: "Waiting for Web confirmation...",
      });
    }

    const baseUrl =
      process.env.NEXUS_PUBLIC_URL ||
      (process.env.NEXT_PUBLIC_VERCEL_URL
        ? `https://${process.env.NEXT_PUBLIC_VERCEL_URL}`
        : "http://localhost:3000");

    return NextResponse.json({
      status: "success",
      access_token: agent.authToken ?? agent.id,
      layer1_public_key: null,
      instance_id: agent.id,
      nexus_base_url: baseUrl,
    });
  } catch (e) {
    console.error("[pairing/status] Error:", e);
    return NextResponse.json(
      { error: "配对状态查询失败" },
      { status: 500 }
    );
  }
}
