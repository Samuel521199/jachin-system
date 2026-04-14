/**
 * 将已上架商品从商店下架（plugins_registry.status → archived），不删本地文件。
 *
 * 依赖：cloud/nexus/.env.local 中 JACHIN_DEV_ID 与发布该插件时一致。
 * 鉴权：请求头 X-Developer-Id（与 store/unpublish 一致）。
 *
 * 用法：
 *   node scripts/unpublish-store-plugin.cjs com.jachin.edu.composition.assistant
 *   npm run store:unpublish -- com.jachin.edu.composition.assistant
 *
 * 可选环境变量：NEXUS_URL（默认 http://localhost:3000）
 */
const path = require("path");

try {
  require("dotenv").config({ path: path.join(__dirname, "../.env.local") });
} catch {
  /* optional */
}
try {
  require("dotenv").config({ path: path.join(__dirname, "../.env") });
} catch {
  /* optional */
}

const devId = (process.env.JACHIN_DEV_ID || "").trim();
const baseUrl = (process.env.NEXUS_URL || "http://localhost:3000").replace(/\/$/, "");
const pluginId = (process.argv[2] || "").trim();

async function main() {
  if (!pluginId) {
    console.error(
      "用法: node scripts/unpublish-store-plugin.cjs <plugin_id>\n" +
        "示例（作文辅助）: node scripts/unpublish-store-plugin.cjs com.jachin.edu.composition.assistant"
    );
    process.exit(1);
  }
  if (!devId) {
    console.error("[unpublish] 请在 .env.local 配置 JACHIN_DEV_ID（须与上架时一致）。");
    process.exit(1);
  }

  const res = await fetch(`${baseUrl}/api/v1/store/unpublish`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Developer-Id": devId,
    },
    body: JSON.stringify({ plugin_id: pluginId }),
  });
  const raw = await res.text();
  let data = {};
  try {
    data = JSON.parse(raw);
  } catch {
    console.error(`[unpublish] HTTP ${res.status}，响应非 JSON：`);
    console.error(raw.slice(0, 800));
    process.exit(1);
  }
  if (!res.ok) {
    console.error(
      `[unpublish] HTTP ${res.status}:`,
      data.message || data.error || data.fallback_error || JSON.stringify(data)
    );
    process.exit(1);
  }
  console.log("[unpublish] OK:", data.message || "", data.id || "");
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
