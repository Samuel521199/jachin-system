import { NextRequest, NextResponse } from "next/server";
import { randomUUID } from "crypto";
import { getSupabase, isSupabaseConfigured } from "@/lib/supabase";

const CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"; // 易读，排除 0/O, 1/I
const CODE_LEN = 6;
const EXPIRE_SEC = 300;

function generateShortCode(): string {
  let s = "";
  for (let i = 0; i < CODE_LEN; i++) {
    s += CHARS[Math.floor(Math.random() * CHARS.length)];
  }
  return s;
}

/**
 * POST /api/v1/pairing/request
 * 阶段 1：Layer 2 发起配对请求，获取 6 位码
 * 详见 docs/PAIRING_PROTOCOL_SPEC.md
 */
export async function POST(req: NextRequest) {
  try {
    const body = await req.json().catch(() => ({}));
    const {
      device_fingerprint,
      temp_public_key,
      environment_type = "bare_metal",
      core_version = "1.0.0",
    } = body;

    const sessionId = randomUUID();
    const shortCode = generateShortCode();
    const expiresAt = new Date(Date.now() + EXPIRE_SEC * 1000);

    if (isSupabaseConfigured()) {
      const sb = getSupabase()!;
      const { error } = await sb.from("pairing_sessions").insert({
        session_id: sessionId,
        short_code: shortCode,
        status: "pending",
        device_info: {
          device_fingerprint: device_fingerprint ?? null,
          temp_public_key: temp_public_key ?? null,
          environment_type,
          core_version,
        },
        expires_at: expiresAt.toISOString(),
      });

      if (error) {
        console.error("[pairing/request] DB error:", error);
        return NextResponse.json(
          { error: "配对请求失败" },
          { status: 500 }
        );
      }
    }

    const baseUrl =
      process.env.NEXUS_PUBLIC_URL ||
      (process.env.VERCEL_URL ? `https://${process.env.VERCEL_URL}` : "http://localhost:3000");
    const pairUrl = `${baseUrl}/pair`;

    return NextResponse.json({
      session_id: sessionId,
      short_code: shortCode,
      expires_in: EXPIRE_SEC,
      pair_url: pairUrl,
    });
  } catch (e) {
    console.error("[pairing/request] Error:", e);
    return NextResponse.json(
      { error: "配对请求失败" },
      { status: 500 }
    );
  }
}
