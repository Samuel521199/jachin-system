/**
 * 开发用：将 users.password_hash 设为与注册 API 相同的 bcrypt（cost 12）。
 * 须在 .env.local 中设置 NEXUS_ALLOW_CLI_PASSWORD_RESET=1（或 true），防止误在生产执行。
 *
 * 用法（在 cloud/nexus 下）:
 *   npx tsx scripts/set-user-password.ts ai_robot@herontech.net 12345678
 */
import { config } from "dotenv";
import postgres from "postgres";
import bcrypt from "bcryptjs";
import path from "path";

config({ path: path.join(process.cwd(), ".env.local") });

const allowed =
  process.env.NEXUS_ALLOW_CLI_PASSWORD_RESET === "true" ||
  process.env.NEXUS_ALLOW_CLI_PASSWORD_RESET === "1";

const url = process.env.DATABASE_URL?.trim();
const emailArg = (process.argv[2] ?? "").trim().toLowerCase();
const plain = process.argv[3] ?? "";

if (!allowed) {
  console.error(
    "拒绝执行：请在 .env.local 设置 NEXUS_ALLOW_CLI_PASSWORD_RESET=1 后重试（仅限本机开发，用毕删除）。"
  );
  process.exit(1);
}
if (!url) {
  console.error("缺少 DATABASE_URL，请在 cloud/nexus 下执行且存在 .env.local");
  process.exit(1);
}
/** 顶层已校验；单独 const 供嵌套函数内使用，避免 TS 不把收窄传播进 main() */
const databaseUrl: string = url;
if (!emailArg || !plain) {
  console.error("用法: npx tsx scripts/set-user-password.ts <邮箱> <新密码>");
  process.exit(1);
}
if (plain.length < 8) {
  console.error("密码至少 8 位（与注册接口一致）");
  process.exit(1);
}

async function main() {
  const sql = postgres(databaseUrl, { max: 1, connect_timeout: 15 });
  try {
    const rows = await sql`
      SELECT id, email FROM users WHERE lower(trim(email)) = ${emailArg} LIMIT 1
    `;
    if (!rows.length) {
      console.error("未找到该邮箱用户:", emailArg);
      process.exit(2);
    }
    const row = rows[0] as { id: string; email: string | null };
    const passwordHash = await bcrypt.hash(plain, 12);
    await sql`
      UPDATE users
      SET password_hash = ${passwordHash}, email = ${emailArg}
      WHERE id = ${row.id}
    `;
    console.log("已更新 password_hash，邮箱规范化为:", emailArg, "user id:", row.id);
    const ok = await bcrypt.compare(plain, passwordHash);
    console.log("自检 bcrypt.compare:", ok);
    process.exit(0);
  } finally {
    await sql.end({ timeout: 5 });
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
