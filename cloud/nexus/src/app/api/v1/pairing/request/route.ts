import { NextRequest, NextResponse } from "next/server";
import { randomUUID } from "crypto";
import { getDb, isDatabaseConfigured } from "@/db";
import { error as logError } from "@/lib/console-utc";
import { edgeAgents } from "@/db/schema";
import {
  pairingStoreSet,
  pairingStoreCleanup,
} from "@/lib/pairing-store";

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
 * Drizzle ORM：写入 edge_agents 表
 */
export async function POST(req: NextRequest) {
  try {
    const body = await req.json().catch(() => ({}));
    const { name } = body;

    const shortCode = generateShortCode();
    const expiresAt = new Date(Date.now() + EXPIRE_SEC * 1000);

    if (isDatabaseConfigured()) {
      const db = getDb()!;

      // 确保 pairing_code 唯一（极小概率冲突则重试）
      let inserted = false;
      let agentId = "";
      let finalCode = shortCode;
      for (let retry = 0; retry < 3 && !inserted; retry++) {
        finalCode = retry > 0 ? generateShortCode() : shortCode;
        try {
          const [row] = await db
            .insert(edgeAgents)
            .values({
              pairingCode: finalCode,
              status: "pending",
              name: name ?? `边缘智能体-${finalCode}`,
              pairingExpiresAt: expiresAt,
            })
            .returning({ id: edgeAgents.id });
          agentId = row?.id ?? "";
          inserted = true;
        } catch (e: unknown) {
          const err = e as { code?: string };
          if (err?.code === "23505") continue; // unique violation
          logError("[pairing/request] DB error:", e);
          return NextResponse.json(
            { error: "配对请求失败" },
            { status: 500 }
          );
        }
      }

      const baseUrl =
        process.env.NEXUS_PUBLIC_URL ||
        (process.env.VERCEL_URL ? `https://${process.env.VERCEL_URL}` : "http://localhost:3000");
      const pairUrl = `${baseUrl}/console/pair`;

      return NextResponse.json({
        session_id: agentId,
        short_code: finalCode,
        expires_in: EXPIRE_SEC,
        pair_url: pairUrl,
      });
    }

    // 内存回退（无 DATABASE_URL 时）
    const sessionId = randomUUID();
    pairingStoreCleanup();
    pairingStoreSet({
      session_id: sessionId,
      short_code: shortCode,
      status: "pending",
      expires_at: expiresAt.toISOString(),
    });

    const baseUrl =
      process.env.NEXUS_PUBLIC_URL ||
      (process.env.VERCEL_URL ? `https://${process.env.VERCEL_URL}` : "http://localhost:3000");
    const pairUrl = `${baseUrl}/console/pair`;

    return NextResponse.json({
      session_id: sessionId,
      short_code: shortCode,
      expires_in: EXPIRE_SEC,
      pair_url: pairUrl,
    });
  } catch (e) {
    logError("[pairing/request] Error:", e);
    return NextResponse.json(
      { error: "配对请求失败" },
      { status: 500 }
    );
  }
}
