/**
 * 重置 Nexus 数据库表（删除后需运行 npm run db:push 重建）
 */
import "dotenv/config";
import { config } from "dotenv";
config({ path: ".env.local" });

import postgres from "postgres";

const url = process.env.DATABASE_URL;
if (!url) {
  console.error("❌ DATABASE_URL 未配置，请检查 .env.local");
  process.exit(1);
}

const sql = postgres(url);

async function reset() {
  console.log("🔄 正在重置数据库表...");
  try {
    await sql.unsafe(`
      DROP TABLE IF EXISTS agent_message_queue CASCADE;
      DROP TABLE IF EXISTS deploy_commands CASCADE;
      DROP TABLE IF EXISTS plugins_registry CASCADE;
      DROP TABLE IF EXISTS transactions CASCADE;
      DROP TABLE IF EXISTS edge_agents CASCADE;
      DROP TABLE IF EXISTS blueprints CASCADE;
      DROP TABLE IF EXISTS organization_users CASCADE;
      DROP TABLE IF EXISTS accounts CASCADE;
      DROP TABLE IF EXISTS sessions CASCADE;
      DROP TABLE IF EXISTS verification_tokens CASCADE;
      DROP TABLE IF EXISTS organizations CASCADE;
      DROP TABLE IF EXISTS users CASCADE;
      DROP TYPE IF EXISTS edge_agent_status CASCADE;
      DROP TYPE IF EXISTS org_role CASCADE;
    `);
    console.log("✅ 表已删除");
    console.log("   请运行: npm run db:push");
  } catch (e) {
    console.error("❌ 重置失败:", e);
    process.exit(1);
  } finally {
    await sql.end();
  }
}

reset();
