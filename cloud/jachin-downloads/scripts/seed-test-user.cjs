/**
 * 在「与 Nexus 共用」的 Postgres 中插入或更新本地测试账号（仅开发自测）。
 * 用法（在 cloud/jachin-downloads 目录）：
 *   node scripts/seed-test-user.cjs
 * 可选环境变量：SEED_EMAIL、SEED_PASSWORD、DATABASE_URL（未设则从 .env.local / .env 读取 DATABASE_URL）
 */
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const postgres = require("postgres");
const bcrypt = require("bcryptjs");

function readDatabaseUrlFromEnvFiles() {
  for (const name of [".env.local", ".env"]) {
    const p = path.join(process.cwd(), name);
    if (!fs.existsSync(p)) continue;
    const text = fs.readFileSync(p, "utf8");
    for (const line of text.split("\n")) {
      const t = line.trim();
      if (t.startsWith("#") || !t.includes("=")) continue;
      const i = t.indexOf("=");
      const key = t.slice(0, i).trim();
      if (key !== "DATABASE_URL") continue;
      let val = t.slice(i + 1).trim();
      if (
        (val.startsWith('"') && val.endsWith('"')) ||
        (val.startsWith("'") && val.endsWith("'"))
      ) {
        val = val.slice(1, -1);
      }
      return val;
    }
  }
  return process.env.DATABASE_URL;
}

async function main() {
  const url = (process.env.DATABASE_URL || readDatabaseUrlFromEnvFiles() || "").trim();
  if (!url || url.length < 8) {
    console.error("未找到 DATABASE_URL。请在 .env.local 中配置，或执行：");
    console.error('  $env:DATABASE_URL="postgresql://..."; node scripts/seed-test-user.cjs');
    process.exit(1);
  }

  const email = (process.env.SEED_EMAIL || "downloads-test@jachin.local").trim().toLowerCase();
  const password = process.env.SEED_PASSWORD || "JachinDownloadsTest!2026";

  const sql = postgres(url);
  try {
    const hash = await bcrypt.hash(password, 12);
    const [row] = await sql`select id from users where email = ${email} limit 1`;
    if (row) {
      await sql`update users set password_hash = ${hash} where email = ${email}`;
      console.log("已更新该邮箱的密码:", email);
    } else {
      const id = crypto.randomUUID();
      await sql`insert into users (id, email, password_hash, name) values (${id}, ${email}, ${hash}, ${"Downloads Test"})`;
      console.log("已创建用户:", email);
    }
    console.log("");
    console.log("登录「桌面端发行大厅」请使用：");
    console.log("  邮箱:", email);
    console.log("  密码:", password);
    console.log("");
    console.log("说明：默认密码仅用于本地测试；生产环境请勿使用或务必改密。");
  } finally {
    await sql.end({ timeout: 5 });
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
