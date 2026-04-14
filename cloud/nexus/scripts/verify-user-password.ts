/**
 * 一次性诊断：从 DB 读用户 password_hash，对给定明文做 bcrypt.compare。
 * 用法（在 cloud/nexus 下）: npx tsx scripts/verify-user-password.ts vivian@herontech.net 12345678
 */
import { config } from "dotenv";
import postgres from "postgres";
import bcrypt from "bcryptjs";
import path from "path";

config({ path: path.join(process.cwd(), ".env.local") });
const url = process.env.DATABASE_URL?.trim();
const emailArg = (process.argv[2] ?? "vivian@herontech.net").trim().toLowerCase();
const plain = process.argv[3] ?? "12345678";

if (!url) {
  console.error("缺少 DATABASE_URL，请在 cloud/nexus 下执行且存在 .env.local");
  process.exit(1);
}
const databaseUrl: string = url;

async function main() {
  const sql = postgres(databaseUrl, { max: 1, connect_timeout: 15 });
  try {
    const rows = await sql`
      SELECT email, password_hash
      FROM users
      WHERE lower(trim(email)) = ${emailArg}
      LIMIT 1
    `;
    console.log("matched_rows:", rows.length);
    const row = rows[0] as { email: string; password_hash: string | null } | undefined;
    if (!row) {
      console.log(
        "结论：未找到该邮箱（lower(trim(email)) 匹配）。若你认为账号存在，检查是否连错 DATABASE_URL/库。"
      );
      process.exit(2);
    }
    const h = (row.password_hash ?? "").trim();
    console.log("db_email_repr:", JSON.stringify(row.email));
    console.log("hash_len:", h.length, "prefix:", h.slice(0, 7));
    const ok = await bcrypt.compare(plain, h);
    console.log(`bcrypt.compare(${JSON.stringify(plain)}):`, ok);
    if (ok) {
      console.log("结论：明文密码与库中哈希一致，Credentials 登录应能通过（若仍失败查 AUTH_SECRET/多实例/缓存）。");
    } else {
      console.log(
        "结论：已查到用户，但此明文与 password_hash 不一致（不是「没查到」）。需重设密码：注册页 + NEXUS_ALLOW_REGISTER_PASSWORD_OVERWRITE，或 scripts/set-user-password.ts + NEXUS_ALLOW_CLI_PASSWORD_RESET=1。"
      );
    }
    process.exit(ok ? 0 : 3);
  } finally {
    await sql.end({ timeout: 5 });
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
