/**
 * 批量将仓库内 MCP stub、HR 原子 MCP、以及带 SKILL.md 的声明式技能发布到 L1 商店。
 *
 * 前置：Postgres + Nexus 已启动；cloud/nexus/.env.local 已配置且与 publish 路由一致：
 *   JACHIN_DEV_TOKEN=...
 *   JACHIN_DEV_ID=...
 * 可选：NEXUS_URL=http://localhost:3000
 * 可选：NEXUS_AUTO_APPROVE=1（本机自动审核为 approved，否则为 pending）
 *
 * 顺序：先全部 MCP（含 com.jachin.hr.recruitment），再 SKILL（077 要求依赖 MCP 已入库），最后 TOOL 元数据包（如天气 util:get_weather_lite 对应 com.jachin.tool.util_weather_lite）。
 * 说明：util:get_weather_lite 已在 L3 内置并在商店「原子工具」展示；上架本条仅为 plugins_registry 登记，目录 API 会与内置去重避免双卡片。
 *
 * 用法：
 *   cd cloud/nexus && node scripts/bulk-publish-store.cjs
 *   node scripts/bulk-publish-store.cjs --dry-run
 */
const fs = require("fs");
const path = require("path");
const AdmZip = require("adm-zip");

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

const repoRoot = path.join(__dirname, "..", "..", "..");
const skillsRepo = path.join(repoRoot, "skills_repo");

const dryRun = process.argv.includes("--dry-run");

/** 去掉 UTF-8 BOM，避免 L1 parse plugin.json 报 Unexpected token '﻿' */
function stripUtf8Bom(buf) {
  if (!Buffer.isBuffer(buf) || buf.length < 3) return buf;
  if (buf[0] === 0xef && buf[1] === 0xbb && buf[2] === 0xbf) return buf.subarray(3);
  return buf;
}

function zipSinglePluginJson(absDir) {
  const pj = path.join(absDir, "plugin.json");
  if (!fs.existsSync(pj)) {
    throw new Error("missing plugin.json: " + pj);
  }
  const zip = new AdmZip();
  zip.addFile("plugin.json", stripUtf8Bom(fs.readFileSync(pj)));
  return zip.toBuffer();
}

function zipDirFiltered(absDir, skipNames) {
  const skip = new Set(skipNames);
  const zip = new AdmZip();
  function walk(d, prefix) {
    for (const name of fs.readdirSync(d)) {
      if (skip.has(name)) continue;
      const full = path.join(d, name);
      const rel = prefix ? prefix + "/" + name : name;
      const st = fs.statSync(full);
      if (st.isDirectory()) {
        walk(full, rel);
      } else {
        zip.addFile(rel.replace(/\\/g, "/"), stripUtf8Bom(fs.readFileSync(full)));
      }
    }
  }
  walk(absDir, "");
  return zip.toBuffer();
}

function zipSkillPackage(meta) {
  const zip = new AdmZip();
  const pluginJson = {
    id: meta.id,
    name: meta.name,
    version: meta.version || "1.0.0",
    description: meta.description,
    item_type: "SKILL",
    runtime_tier: "L3_LOCAL",
    required_mcps: meta.required_mcps || [],
  };
  zip.addFile("plugin.json", Buffer.from(JSON.stringify(pluginJson, null, 2), "utf8"));
  const mdPath = path.join(skillsRepo, meta.skillMd);
  if (!fs.existsSync(mdPath)) {
    throw new Error("missing SKILL.md: " + mdPath);
  }
  zip.addFile("SKILL.md", stripUtf8Bom(fs.readFileSync(mdPath)));
  return zip.toBuffer();
}

/** @type {{ label: string, zip: () => Buffer }[]} */
const jobs = [];

// —— MCP：官方/示例 stub（仅 plugin.json），目录名排序保证顺序稳定 ——
const stubRoot = path.join(skillsRepo, "l1_upload_stubs");
if (fs.existsSync(stubRoot)) {
  const names = fs
    .readdirSync(stubRoot)
    .filter((name) => {
      const d = path.join(stubRoot, name);
      return fs.statSync(d).isDirectory() && fs.existsSync(path.join(d, "plugin.json"));
    })
    .sort();
  for (const name of names) {
    const d = path.join(stubRoot, name);
    jobs.push({
      label: `MCP stub ${name}`,
      zip: () => zipSinglePluginJson(d),
    });
  }
}

// —— MCP：HR 原子工具（与 install.py / hr-atomic-tools 同源）——
const hrAtomicDir = path.join(skillsRepo, "plugin", "com.jachin.hr.recruitment");
if (fs.existsSync(hrAtomicDir)) {
  jobs.push({
    label: "MCP com.jachin.hr.recruitment (hr-atomic-tools)",
    zip: () =>
      zipDirFiltered(hrAtomicDir, ["__pycache__", ".pytest_cache", "node_modules", ".git"]),
  });
}

// —— SKILL：声明式 SKILL.md ——
const skillDefs = [
  {
    id: "com.jachin.skill.hr.recruitment",
    name: "HR 招聘总监",
    description:
      "新职位发帖（JD+Boss）与已有岗轻量收网；多轮确认；依赖 com.jachin.hr.recruitment MCP。",
    skillMd: "hr-recruitment/SKILL.md",
    required_mcps: ["mcp:com.jachin.hr.recruitment"],
  },
  {
    id: "com.jachin.skill.hr.recruiter",
    name: "招聘助手 v2",
    description: "Boss 雷达、收件箱、归档、PDF、虫群评审等（4-track-b-skill）。",
    skillMd: "plugin/4-track-b-skill/SKILL.md",
    required_mcps: ["mcp:com.jachin.hr.recruitment"],
  },
  {
    id: "com.jachin.skill.hr.job.manager",
    name: "HR 招聘岗位发布",
    description: "自然语言解析 JD、发布岗位、烙印规则（hr-job-manager）。",
    skillMd: "plugin/4-track-b-skill/hr-job-manager/SKILL.md",
    required_mcps: ["mcp:com.jachin.hr.recruitment"],
  },
  {
    id: "com.jachin.skill.hr.progress.query",
    name: "HR 进度查询",
    description: "回答「收了多少简历」「进度」等（hr-progress-query）。",
    skillMd: "plugin/4-track-b-skill/hr-progress-query/SKILL.md",
    required_mcps: ["mcp:com.jachin.hr.recruitment"],
  },
  {
    id: "com.jachin.skill.workspace.inspector",
    name: "Workspace 巡检",
    description: "检查 ~/.jachin/workspace/；优先 MCP 或 core:fs_read / core:shell_exec。",
    skillMd: "workspace_inspector/SKILL.md",
    required_mcps: [],
  },
  {
    id: "com.jachin.bi.analysis",
    name: "BI 分析",
    description:
      "业务指标与每日战报：抓取、指标引擎、飞书/邮件推送；配置见 com.jachin.bi.daily_report。",
    skillMd: "com.jachin.bi.analysis/SKILL.md",
    required_mcps: [],
  },
];

for (const s of skillDefs) {
  jobs.push({
    label: `SKILL ${s.id}`,
    zip: () => zipSkillPackage(s),
  });
}

// —— TOOL：仅 plugin.json（与 L3 内置 util 对齐的货架登记，见 l1_upload_stubs_tools）——
const toolStubRoot = path.join(skillsRepo, "l1_upload_stubs_tools");
if (fs.existsSync(toolStubRoot)) {
  const toolNames = fs
    .readdirSync(toolStubRoot)
    .filter((name) => {
      const d = path.join(toolStubRoot, name);
      return fs.statSync(d).isDirectory() && fs.existsSync(path.join(d, "plugin.json"));
    })
    .sort();
  for (const name of toolNames) {
    const d = path.join(toolStubRoot, name);
    jobs.push({
      label: `TOOL ${name}`,
      zip: () => zipSinglePluginJson(d),
    });
  }
}

async function main() {
  const token = (process.env.JACHIN_DEV_TOKEN || "").trim();
  const baseUrl = (process.env.NEXUS_URL || "http://localhost:3000").replace(/\/$/, "");

  if (!dryRun && !token) {
    console.error(
      "[bulk-publish] 缺少 JACHIN_DEV_TOKEN。请在 cloud/nexus/.env.local 配置 JACHIN_DEV_TOKEN 与 JACHIN_DEV_ID。"
    );
    process.exit(1);
  }

  console.log(
    `[bulk-publish] ${dryRun ? "DRY-RUN" : "POST"} ${jobs.length} 个包 → ${baseUrl}/api/v1/store/publish`
  );

  let ok = 0;
  let fail = 0;
  let saw401 = false;

  for (let i = 0; i < jobs.length; i++) {
    const j = jobs[i];
    const buf = j.zip();
    if (dryRun) {
      console.log(`  [${i + 1}/${jobs.length}] ${j.label} (${buf.length} bytes)`);
      ok++;
      continue;
    }

    const form = new FormData();
    form.append("package", new Blob([buf], { type: "application/zip" }), "package.zip");
    form.append("visibility", "PUBLIC");
    form.append("price_monthly", "0");

    try {
      const res = await fetch(`${baseUrl}/api/v1/store/publish`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        body: form,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.success === false) {
        if (res.status === 401 && !saw401) {
          saw401 = true;
          console.error(
            "  (401：请在 cloud/nexus/.env.local 设置 JACHIN_DEV_TOKEN / JACHIN_DEV_ID，与 Nexus 进程一致后重启再试。)"
          );
        }
        console.error(`  [FAIL] ${j.label}`, res.status, data.error || data.code || data);
        fail++;
      } else {
        console.log(
          `  [ok] ${j.label} → ${data.plugin_id} (${data.status || "?"})`
        );
        ok++;
      }
    } catch (e) {
      console.error(`  [FAIL] ${j.label}`, e.message || e);
      fail++;
    }
  }

  console.log(`[bulk-publish] 完成：成功 ${ok}，失败 ${fail}`);
  if (fail > 0) process.exit(1);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
