import { NextRequest, NextResponse } from "next/server";
import bcrypt from "bcryptjs";
import { eq, sql } from "drizzle-orm";
import { describeDatabaseConnectError, getDb, isDatabaseConfigured } from "@/db";
import { users } from "@/db/schema";
import { registerUserOnly } from "@/lib/auth/genesis";
import { passwordPlainForCredentials } from "@/lib/auth/credentials-password";
import { credentialsHashUsable } from "@/lib/auth/password-hash";

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/**
 * POST /api/auth/register
 * 邮箱 + 密码注册：仅创建 users；工作区须登录后在 /console/workspace 创建或加入。
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
    const password = passwordPlainForCredentials(body.password);
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
    const [existing] = await db
      .select({ id: users.id, passwordHash: users.passwordHash })
      .from(users)
      .where(sql`lower(trim(${users.email})) = ${email}`)
      .limit(1);

    const passwordHash = await bcrypt.hash(password, 12);

    const devForcePw =
      process.env.NEXUS_ALLOW_REGISTER_PASSWORD_OVERWRITE === "true" ||
      process.env.NEXUS_ALLOW_REGISTER_PASSWORD_OVERWRITE === "1";

    if (existing) {
      const stored = (existing.passwordHash ?? "").trim();
      const weak = !credentialsHashUsable(stored);
      if (weak || devForcePw) {
        if (devForcePw && !weak) {
          console.warn(
            "[register] NEXUS_ALLOW_REGISTER_PASSWORD_OVERWRITE：已覆盖已有 bcrypt 密码 email=",
            email
          );
        }
        await db
          .update(users)
          .set({
            passwordHash,
            email,
            ...(name ? { name } : {}),
          })
          .where(eq(users.id, existing.id));
        return NextResponse.json({
          success: true,
          userId: existing.id,
          needs_workspace: true,
          message: weak
            ? "该邮箱已存在但未设置有效登录密码（或仅有占位空串）。已为你写入新密码，请直接登录。"
            : "已按开发开关覆盖登录密码，请立即登录并在完成后从 .env.local 删除 NEXUS_ALLOW_REGISTER_PASSWORD_OVERWRITE。",
          password_recovered: true,
        });
      }
      return NextResponse.json(
        {
          success: false,
          error: "EMAIL_TAKEN",
          message:
            "该邮箱已注册。若忘记密码：可在 .env.local 临时设 NEXUS_ALLOW_REGISTER_PASSWORD_OVERWRITE=true，重启 Nexus 后在「注册」页用同一邮箱提交新密码（仅限本机开发），完成后务必删掉该变量。",
        },
        { status: 409 }
      );
    }

    const { userId } = await registerUserOnly(db, {
      email,
      passwordHash,
      name: name || undefined,
    });

    return NextResponse.json({
      success: true,
      userId,
      needs_workspace: true,
      message: "注册成功，请登录后在「工作区」创建或加入组织",
    });
  } catch (e) {
    const dbHint = describeDatabaseConnectError(e);
    if (dbHint) {
      console.warn("[register]", dbHint);
      return NextResponse.json(
        {
          success: false,
          error: "DATABASE_UNAVAILABLE",
          message: dbHint,
        },
        { status: 503 }
      );
    }
    console.error("[register]", e);
    return NextResponse.json(
      { success: false, error: "REGISTER_FAILED", message: "注册失败" },
      { status: 500 }
    );
  }
}
