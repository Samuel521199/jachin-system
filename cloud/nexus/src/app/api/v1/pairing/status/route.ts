import { NextRequest, NextResponse } from "next/server";
import { getSupabase, isSupabaseConfigured } from "@/lib/supabase";
import { pairingStoreGetBySession, pairingStoreGetByCode } from "@/lib/pairing-store";

export const dynamic = "force-dynamic";

/**
 * GET /api/v1/pairing/status?code=XXX 或 ?session_id=XXX
 * 阶段 3：Layer 2 轮询配对状态
 * Supabase 直连：查询 edge_agents，status=active 时返回 auth_token
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

    if (!isSupabaseConfigured()) {
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

    const sb = getSupabase()!;

    let query = sb.from("edge_agents").select("id, status, auth_token, pairing_expires_at, name");

    if (code) {
      query = query.eq("pairing_code", String(code).trim().toUpperCase().replace(/-/g, ""));
    } else {
      query = query.eq("id", sessionId);
    }

    const { data: agent, error } = await query.maybeSingle();

    if (error) {
      console.error("[pairing/status] Query error:", error);
      return NextResponse.json(
        { error: "配对状态查询失败" },
        { status: 500 }
      );
    }

    if (!agent) {
      return NextResponse.json(
        { status: "expired", error: "会话不存在或已失效" },
        { status: 404 }
      );
    }

    if (agent.pairing_expires_at && new Date(agent.pairing_expires_at) < new Date()) {
      await sb
        .from("edge_agents")
        .update({ status: "offline" })
        .eq("id", agent.id);
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
      access_token: agent.auth_token ?? agent.id,
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
