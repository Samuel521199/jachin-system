/**
 * 商店 MCP 卡片的展示文案：标题含功能简称（括号内），副标题为一句话简介。
 * 键为 plugins_registry.plugin_id（与 bulk-publish / stub 的 plugin.json id 一致）。
 */
import type { NexusUiLang } from "@/lib/nexus-ui-i18n";

export type McpStoreDisplay = {
  title: string;
  /** 卡片灰字主文案（一句话） */
  tagline: string;
  /** 可选：数据库里的技术说明，作更小字号展示 */
  technicalNote?: string;
};

type Entry = { zh: { title: string; tagline: string }; en: { title: string; tagline: string } };

const ENTRIES: Record<string, Entry> = {
  "com.jachin.hr.recruitment": {
    zh: {
      title: "HR 原子工具箱（招聘自动化）",
      tagline: "在本机以 Python stdio 提供 Boss、飞书、简历与调度等招聘原子能力。",
    },
    en: {
      title: "HR Atomic Toolkit (Recruiting)",
      tagline: "Python stdio MCP for Boss, Feishu/Lark, resumes, and recruitment scheduling.",
    },
  },
  "com.jachin.mcp.stub.tavily.search": {
    zh: { title: "Tavily Search（联网检索）", tagline: "通过 Tavily 做联网搜索与摘要，需配置 API Key。" },
    en: { title: "Tavily Search (Web search)", tagline: "Web search and snippets via Tavily; requires an API key." },
  },
  "com.jachin.mcp.stub.playwright.browser": {
    zh: { title: "Playwright（浏览器自动化）", tagline: "驱动真实浏览器完成点击、填表与页面抓取。" },
    en: { title: "Playwright (Browser automation)", tagline: "Drive a real browser for clicks, forms, and page capture." },
  },
  "com.jachin.mcp.stub.official.time": {
    zh: { title: "MCP Time（时间）", tagline: "提供当前时间、时区与简单时间换算。" },
    en: { title: "MCP Time (Clock)", tagline: "Current time, time zones, and simple time helpers." },
  },
  "com.jachin.mcp.stub.official.sqlite.npx": {
    zh: { title: "MCP SQLite（本地数据库）", tagline: "在工作区内用 SQLite 做结构化查询与小型数据存储。" },
    en: { title: "MCP SQLite (Local DB)", tagline: "Query and store structured data with SQLite in your workspace." },
  },
  "com.jachin.mcp.stub.official.memory.npx": {
    zh: { title: "MCP Memory（知识记忆）", tagline: "基于官方 Memory 服务的实体图谱式记忆与检索。" },
    en: { title: "MCP Memory (Knowledge graph)", tagline: "Graph-style memory and recall via the official Memory server." },
  },
  "com.jachin.mcp.stub.official.git": {
    zh: { title: "MCP Git（版本库）", tagline: "在指定仓库根目录上执行只读或受控 Git 查询与操作。" },
    en: { title: "MCP Git (Repository)", tagline: "Read-oriented Git queries and safe ops on a repo root." },
  },
  "com.jachin.mcp.stub.official.filesystem.dirs": {
    zh: { title: "MCP Filesystem（工作区文件）", tagline: "在允许的工作区目录内读写文件，适合工具链与素材管理。" },
    en: { title: "MCP Filesystem (Workspace files)", tagline: "Read/write under allowed workspace roots for assets and tools." },
  },
  "com.jachin.mcp.stub.official.fetch": {
    zh: { title: "MCP Fetch（网页抓取）", tagline: "按 URL 抓取正文，遵守站点 robots 等策略。" },
    en: { title: "MCP Fetch (HTTP content)", tagline: "Fetch page text by URL with robots-aware behavior." },
  },
  "com.jachin.mcp.stub.sendmail.smtp": {
    zh: { title: "MCP Sendmail（SMTP 邮件）", tagline: "通过 SMTP 发送邮件，适合告警与通知类集成。" },
    en: { title: "MCP Sendmail (SMTP email)", tagline: "Send mail via SMTP for alerts and integrations." },
  },
  "com.jachin.mcp.stub.sqlite.life.ledger": {
    zh: {
      title: "SQLite 生活库 / 记账",
      tagline: "本地 SQLite 生活库与记账（uvx mcp-server-sqlite），默认库位于工作区 my_life_data.db。",
    },
    en: {
      title: "SQLite Life Ledger",
      tagline: "Local SQLite life DB and ledger via uvx mcp-server-sqlite; default my_life_data.db in workspace.",
    },
  },
  "com.jachin.mcp.stub.google.maps.travel": {
    zh: {
      title: "Google Maps（出行助手）",
      tagline: "地理编码、路线、周边地点；需配置 GOOGLE_MAPS_API_KEY。",
    },
    en: {
      title: "Google Maps (Travel)",
      tagline: "Geocoding, routes, and places; requires GOOGLE_MAPS_API_KEY.",
    },
  },
};

export function getMcpStoreDisplay(
  pluginId: string | null | undefined,
  fallbackName: string,
  fallbackDesc: string | null,
  lang: NexusUiLang
): McpStoreDisplay {
  const id = (pluginId ?? "").trim();
  const e = id ? ENTRIES[id] : undefined;
  if (!e) {
    return {
      title: fallbackName,
      tagline: fallbackDesc?.trim() || "",
      technicalNote: undefined,
    };
  }
  const loc = lang === "en" ? e.en : e.zh;
  return {
    title: loc.title,
    tagline: loc.tagline,
    technicalNote: fallbackDesc?.trim() || undefined,
  };
}
