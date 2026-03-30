import { NextRequest, NextResponse } from "next/server";
import bcrypt from "bcryptjs";
import { eq } from "drizzle-orm";
import { getDb, isDatabaseConfigured } from "@/db";
import { users } from "@/db/schema";
import { registerUserWithGenesis } from "@/lib/auth/genesis";

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/**
 * POST /api/auth/register
 * 邮箱 + 密码注册：单事务写入 users + 个人组织 + organization_users.owner（零感知生根）
 */
export async function POST(req: NextRequest) {
  try {
    if (!isDatabaseConfigured()) {
      return NextResponse.json(
        { success: false, error: "DATABASE_UNAVAILABLE", message: "未配置 DATABASE_URL" },
        { status: 503 }
      );
    }
    const body = (await req.json()) as { email?: string; password?: string; name?: string };
    const email = typeof body.email === "string" ? body.email.trim().toLowerCase() : "";
    const password = typeof body.password === "string" ? body.password : "";
    const name = typeof body.name === "string" ? body.name.trim() : undefined;

    if (!email || !EMAIL_RE.test(email)) {
      return NextResponse.json(
        { success: false, error: "INVALID_EMAIL", message: "请输入有效邮箱" },
        { status: 400 }
      );
    }
    if (password.length < 8) {
      return NextResponse.json(
        { success: false, error: "WEAK_PASSWORD", message: "密码至少 8 位" },
        { status: 400 }
      );
    }

    const db = getDb()!;
    const [existing] = await db.select({ id: users.id }).from(users).where(eq(users.email, email)).limit(1);
    if (existing) {
      return NextResponse.json(
        { success: false, error: "EMAIL_TAKEN", message: "该邮箱已注册" },
        { status: 409 }
      );
    }

    const passwordHash = await bcrypt.hash(password, 12);
    const { userId, orgId } = await registerUserWithGenesis(db, {
      email,
      passwordHash,
      name: name || undefined,
    });

    return NextResponse.json({
      success: true,
      userId,
      orgId,
      message: "注册成功，请登录",
    });
  } catch (e) {
    console.error("[register]", e);
    return NextResponse.json(
      { success: false, error: "REGISTER_FAILED", message: "注册失败" },
      { status: 500 }
    );
  }
}
