/**
 * 单独发布 skills_repo/l1_upload_stubs_tools/com.jachin.tool.util_weather_lite
 *
 *   cd cloud/nexus
 *   $env:NEXUS_URL = "http://47.86.39.173:3000"
 *   $env:JACHIN_DEV_TOKEN = "..."
 *   node scripts/republish-util-weather.cjs
 */
const fs = require("fs");
const path = require("path");
const AdmZip = require("adm-zip");

try {
  require("dotenv").config({ path: path.join(__dirname, "../.env.local") });
} catch {
  /* optional */
}

const repoRoot = path.join(__dirname, "..", "..", "..");
const d = path.join(
  repoRoot,
  "skills_repo",
  "l1_upload_stubs_tools",
  "com.jachin.tool.util_weather_lite"
);
const z = new AdmZip();
z.addFile("plugin.json", fs.readFileSync(path.join(d, "plugin.json")));
const buf = z.toBuffer();

const baseUrl = (process.env.NEXUS_URL || "http://localhost:3000").replace(/\/$/, "");
const token = (process.env.JACHIN_DEV_TOKEN || "").trim();
if (!token) {
  console.error("[republish-util-weather] 缺少 JACHIN_DEV_TOKEN");
  process.exit(1);
}

async function main() {
  const form = new FormData();
  form.append("package", new Blob([buf], { type: "application/zip" }), "package.zip");
  form.append("visibility", "PUBLIC");
  form.append("price_monthly", "0");

  const res = await fetch(`${baseUrl}/api/v1/store/publish`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: form,
  });
  const data = await res.json().catch(() => ({}));
  console.log(JSON.stringify({ status: res.status, body: data }, null, 2));
  if (!res.ok || data.success === false) process.exit(1);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
