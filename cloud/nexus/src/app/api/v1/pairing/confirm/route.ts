import { NextRequest, NextResponse } from "next/server";
import { randomUUID } from "crypto";
import { getSupabase, isSupabaseConfigured } from "@/lib/supabase";

const DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000001";

/**
 * POST /api/v1/pairing/confirm
 * 阶段 2：用户在 Web Console 输入 6 位码，授权绑定
 * 需登录态，body 可传 user_id；未配置时使用默认用户
 * 详见 docs/PAIRING_PROTOCOL_SPEC.md
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

    const shortCode = String(code).trim().toUpperCase();

    if (!isSupabaseConfigured()) {
      return NextResponse.json({
        success: true,
        instance_id: "dev-layer2-001",
        message: "Dev mode: pairing simulated",
      });
    }

    const sb = getSupabase()!;
    const userId = bodyUserId ?? DEFAULT_USER_ID;

    const { data: session, error: findErr } = await sb
      .from("pairing_sessions")
      .select("session_id, status, expires_at, layer2_instance_id")
      .eq("short_code", shortCode)
      .maybeSingle();

    if (findErr) {
      console.error("[pairing/confirm] Query error:", findErr);
      return NextResponse.json(
        { success: false, error: "配对验证失败" },
        { status: 500 }
      );
    }

    if (!session) {
      return NextResponse.json(
        { success: false, error: "配对码无效或已过期" },
        { status: 404 }
      );
    }

    if (session.status === "approved") {
      const { data: linked } = await sb
        .from("layer2_instances")
        .select("instance_id")
        .eq("id", session.layer2_instance_id)
        .maybeSingle();
      return NextResponse.json({
        success: true,
        instance_id: linked?.instance_id ?? "dev-layer2-001",
      });
    }

    if (new Date(session.expires_at) < new Date()) {
      await sb
        .from("pairing_sessions")
        .update({ status: "expired" })
        .eq("session_id", session.session_id);
      return NextResponse.json(
        { success: false, error: "配对码已过期" },
        { status: 410 }
      );
    }

    const instanceId = `jachin-${randomUUID().slice(0, 8)}`;

    const { data: newInstance, error: insertErr } = await sb
      .from("layer2_instances")
      .insert({
        instance_id: instanceId,
        owner_id: userId,
        environment_type: "bare_metal",
        core_version: "1.0.0",
      })
      .select("id")
      .single();

    if (insertErr) {
      console.error("[pairing/confirm] layer2_instances insert:", insertErr);
      return NextResponse.json(
        { success: false, error: "注册边缘智能体失败" },
        { status: 500 }
      );
    }

    const { error: updateErr } = await sb
      .from("pairing_sessions")
      .update({
        status: "approved",
        user_id: userId,
        layer2_instance_id: newInstance.id,
      })
      .eq("session_id", session.session_id);

    if (updateErr) {
      console.error("[pairing/confirm] pairing_sessions update:", updateErr);
      return NextResponse.json(
        { success: false, error: "授权更新失败" },
        { status: 500 }
      );
    }

    return NextResponse.json({
      success: true,
      instance_id: instanceId,
    });
  } catch (e) {
    console.error("[pairing/confirm] Error:", e);
    return NextResponse.json(
      { success: false, error: "配对确认失败" },
      { status: 500 }
    );
  }
}
