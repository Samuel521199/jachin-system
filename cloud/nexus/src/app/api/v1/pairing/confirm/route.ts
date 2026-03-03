import { NextRequest, NextResponse } from "next/server";
import { randomUUID } from "crypto";
import { getSupabase, isSupabaseConfigured } from "@/lib/supabase";
import { createClient } from "@/lib/supabase-auth/server";
import { pairingStoreGetByCode, pairingStoreApproveByCode } from "@/lib/pairing-store";

const DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000001";

/**
 * POST /api/v1/pairing/confirm
 * 阶段 2：用户在 Web Console 输入 6 位码，授权绑定
 * Supabase 直连：更新 edge_agents 为 active，绑定当前登录用户
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

    const shortCode = String(code).trim().toUpperCase().replace(/-/g, "");

    if (!isSupabaseConfigured()) {
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
          message: "Edge Agent successfully paired!",
        });
      }
      const instanceId = `jachin-${randomUUID().slice(0, 8)}`;
      pairingStoreApproveByCode(shortCode, instanceId);
      return NextResponse.json({
        success: true,
        instance_id: instanceId,
        message: "Edge Agent successfully paired!",
      });
    }

    const sb = getSupabase()!;

    // 获取当前登录用户（Supabase Auth）
    let userId = bodyUserId ?? DEFAULT_USER_ID;
    const authClient = await createClient();
    if (authClient) {
      const { data: { user } } = await authClient.getUser();
      if (user?.id) {
        userId = user.id;
      }
    }

    const { data: agent, error: findErr } = await sb
      .from("edge_agents")
      .select("id, status, pairing_expires_at")
      .eq("pairing_code", shortCode)
      .maybeSingle();

    if (findErr) {
      console.error("[pairing/confirm] Query error:", findErr);
      return NextResponse.json(
        { success: false, error: "配对验证失败" },
        { status: 500 }
      );
    }

    if (!agent) {
      return NextResponse.json(
        { success: false, error: "配对码无效或已过期" },
        { status: 404 }
      );
    }

    if (agent.status === "active") {
      return NextResponse.json({
        success: true,
        instance_id: agent.id,
        message: "Edge Agent successfully paired!",
      });
    }

    if (agent.pairing_expires_at && new Date(agent.pairing_expires_at) < new Date()) {
      await sb
        .from("edge_agents")
        .update({ status: "offline" })
        .eq("id", agent.id);
      return NextResponse.json(
        { success: false, error: "配对码已过期" },
        { status: 410 }
      );
    }

    const authToken = `jch-${randomUUID().replace(/-/g, "")}`;

    const { error: updateErr } = await sb
      .from("edge_agents")
      .update({
        status: "active",
        user_id: userId,
        auth_token: authToken,
        pairing_expires_at: null,
      })
      .eq("id", agent.id);

    if (updateErr) {
      console.error("[pairing/confirm] edge_agents update:", updateErr);
      return NextResponse.json(
        { success: false, error: "授权更新失败" },
        { status: 500 }
      );
    }

    return NextResponse.json({
      success: true,
      instance_id: agent.id,
      message: "Edge Agent successfully paired!",
    });
  } catch (e) {
    console.error("[pairing/confirm] Error:", e);
    return NextResponse.json(
      { success: false, error: "配对确认失败" },
      { status: 500 }
    );
  }
}
