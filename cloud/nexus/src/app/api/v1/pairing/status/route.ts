import { NextRequest, NextResponse } from "next/server";
import { getSupabase, isSupabaseConfigured } from "@/lib/supabase";

/**
 * GET /api/v1/pairing/status?session_id=xxx
 * 阶段 3：Layer 2 轮询配对状态，获取 access_token 与 instance_id
 * 详见 docs/PAIRING_PROTOCOL_SPEC.md
 */
export async function GET(req: NextRequest) {
  try {
    const { searchParams } = new URL(req.url);
    const sessionId = searchParams.get("session_id");

    if (!sessionId) {
      return NextResponse.json(
        { error: "Missing session_id" },
        { status: 400 }
      );
    }

    if (!isSupabaseConfigured()) {
      return NextResponse.json({
        status: "pending",
        message: "Dev mode: configure Supabase for real pairing",
      });
    }

    const sb = getSupabase()!;

    const { data: session, error } = await sb
      .from("pairing_sessions")
      .select("session_id, status, expires_at, layer2_instance_id")
      .eq("session_id", sessionId)
      .maybeSingle();

    if (error) {
      console.error("[pairing/status] Query error:", error);
      return NextResponse.json(
        { error: "配对状态查询失败" },
        { status: 500 }
      );
    }

    if (!session) {
      return NextResponse.json(
        { status: "expired", error: "会话不存在或已失效" },
        { status: 404 }
      );
    }

    if (new Date(session.expires_at) < new Date()) {
      await sb
        .from("pairing_sessions")
        .update({ status: "expired" })
        .eq("session_id", sessionId);
      return NextResponse.json({
        status: "expired",
        error: "配对码已过期",
      });
    }

    if (session.status !== "approved") {
      return NextResponse.json({ status: "pending" });
    }

    let instanceId = "dev-layer2-001";
    if (session.layer2_instance_id) {
      const { data: inst } = await sb
        .from("layer2_instances")
        .select("instance_id")
        .eq("id", session.layer2_instance_id)
        .maybeSingle();
      if (inst) instanceId = inst.instance_id;
    }

    const baseUrl =
      process.env.NEXUS_PUBLIC_URL ||
      (process.env.NEXT_PUBLIC_VERCEL_URL
        ? `https://${process.env.NEXT_PUBLIC_VERCEL_URL}`
        : "http://localhost:3000");

    return NextResponse.json({
      status: "success",
      access_token: sessionId,
      layer1_public_key: null,
      instance_id: instanceId,
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
