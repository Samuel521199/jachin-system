/**
 * 本地开发：清空 public schema（等价于删掉库里所有业务表/枚举/函数等），再可选一步 drizzle-kit push 按 schema.ts 重建。
 *
 * 用法:
 *   npm run db:reset              — 仅清空，之后自行 npm run db:push:raw（空库建议 raw，无需 Prisma 预检）
 *   npm run db:fresh              — 清空 + 立即 drizzle-kit push（与 drizzle.config 同一 DATABASE_URL）
 *
 * 会删除 public 下全部对象；勿在生产或有数据的库上执行。
 */
import { config } from "dotenv";
import { join } from "path";
import { spawnSync } from "child_process";
import postgres from "postgres";

const nexusRoot = process.cwd();

config({ path: join(nexusRoot, ".env") });
config({ path: join(nexusRoot, ".env.local"), override: true });

let dbUrl = process.env.DATABASE_URL;
if (!dbUrl) {
  console.error("[reset-db] DATABASE_URL 未配置（.env / .env.local）");
  process.exit(1);
}
if (dbUrl.startsWith("postgres://")) {
  dbUrl = "postgresql://" + dbUrl.slice("postgres://".length);
}

const withPush = process.argv.includes("--with-push");

async function main(): Promise<void> {
  console.log("[reset-db] 将执行 DROP SCHEMA public CASCADE（当前库内 public 全部清空）");
  console.log("[reset-db] 目标:", dbUrl!.replace(/:[^:@/]+@/, ":****@"));

  const sql = postgres(dbUrl!, { max: 1, connect_timeout: 30 });
  try {
    await sql.unsafe(`
      DROP SCHEMA IF EXISTS public CASCADE;
      CREATE SCHEMA public;
      GRANT ALL ON SCHEMA public TO CURRENT_USER;
      GRANT ALL ON SCHEMA public TO PUBLIC;
    `);
    console.log("[reset-db] 已重建空 public schema。");
  } catch (e) {
    console.error("[reset-db] 失败:", e);
    process.exit(1);
  } finally {
    await sql.end({ timeout: 5 }).catch(() => {});
  }

  if (!withPush) {
    console.log("[reset-db] 下一步（空库推荐 raw push，免预检）: npm run db:push:raw");
    console.log("[reset-db] 或: npm run db:fresh（本脚本一键清空+push）");
    return;
  }

  console.log("[reset-db] 正在执行 npx drizzle-kit push …");
  const r = spawnSync("npx", ["drizzle-kit", "push"], {
    cwd: nexusRoot,
    stdio: "inherit",
    env: process.env,
    shell: process.platform === "win32",
  });
  if (r.status !== 0) {
    process.exit(r.status ?? 1);
  }
  console.log("[reset-db] 完成。可 npm run dev；登录需重新注册/配 OAuth（本地数据已空）。");
}

void main();
