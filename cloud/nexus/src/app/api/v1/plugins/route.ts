import { NextRequest, NextResponse } from "next/server";
import { getDb, isDatabaseConfigured } from "@/db";
import { pluginsRegistry } from "@/db/schema";
import { eq, and, desc } from "drizzle-orm";

export const dynamic = "force-dynamic";

export interface PluginRecord {
  id: string;
  plugin_id: string;
  name: string;
  category: string;
  description: string | null;
  download_count: number;
  download_url: string;
  manifest_json?: Record<string, unknown> | null;
  x: number;
  y: number;
  color: string;
  connections: string[];
}

const CATEGORY_COLORS: Record<string, string> = {
  skill: "#22d3ee",
  persona: "#f472b6",
  memory: "#a78bfa",
  default: "#6366f1",
};

/**
 * GET /api/v1/plugins
 * Marketplace API - 神经元商城插件列表
 */
export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url);
    const category = searchParams.get("category");
    const sort = searchParams.get("sort") || "downloads";

    if (!isDatabaseConfigured()) {
      return NextResponse.json(
        { success: true, data: getFallbackPlugins(category, sort), meta: { total: 5, source: "fallback" } },
        { status: 200 }
      );
    }

    const db = getDb()!;

    const whereClause = category
      ? and(eq(pluginsRegistry.status, "approved"), eq(pluginsRegistry.category, category))
      : eq(pluginsRegistry.status, "approved");

    const orderByClause =
      sort === "recent"
        ? desc(pluginsRegistry.createdAt)
        : desc(pluginsRegistry.downloadCount);

    const data = await db
      .select({
        id: pluginsRegistry.id,
        pluginId: pluginsRegistry.pluginId,
        name: pluginsRegistry.name,
        category: pluginsRegistry.category,
        description: pluginsRegistry.description,
        downloadCount: pluginsRegistry.downloadCount,
        downloadUrl: pluginsRegistry.downloadUrl,
        manifestJson: pluginsRegistry.manifestJson,
      })
      .from(pluginsRegistry)
      .where(whereClause)
      .orderBy(orderByClause);

    const plugins = mapToPluginRecords(data);
    return NextResponse.json({
      success: true,
      data: plugins,
      meta: { total: plugins.length },
    });
  } catch (e) {
    console.error("[plugins] Unexpected error:", e);
    return NextResponse.json(
      { success: false, error: "Internal server error" },
      { status: 500 }
    );
  }
}

function mapToPluginRecords(rows: {
  id: string;
  pluginId: string;
  name: string;
  category: string;
  description: string | null;
  downloadCount: number | null;
  downloadUrl: string;
  manifestJson: unknown;
}[]): PluginRecord[] {
  const pluginIds = rows.map((r) => r.pluginId);
  const n = rows.length;
  return rows.map((row, i) => {
    const category = row.category || "skill";
    const angle = (i / n) * 2 * Math.PI - Math.PI / 2;
    const radius = 35 + (i % 3) * 8;
    return {
      id: row.id || row.pluginId,
      plugin_id: row.pluginId,
      name: row.name,
      category,
      description: row.description || null,
      download_count: row.downloadCount ?? 0,
      download_url: row.downloadUrl || "",
      manifest_json: (row.manifestJson as Record<string, unknown>) ?? null,
      x: 50 + radius * Math.cos(angle) + (i % 2 ? 5 : -5),
      y: 50 + radius * Math.sin(angle) + (i % 3 === 1 ? 3 : 0),
      color: CATEGORY_COLORS[category] || CATEGORY_COLORS.default,
      connections: pluginIds.filter((id) => id !== row.pluginId).slice(0, 2),
    };
  });
}

function getFallbackPlugins(category: string | null, sort: string): PluginRecord[] {
  const positions = [
    [35, 25], [65, 35], [50, 65], [20, 55], [80, 55],
  ];
  const all: PluginRecord[] = [
    { id: "python", plugin_id: "com.jachin.python-executor", name: "Python 执行器", category: "skill", description: "在本地沙箱中安全执行 Python 脚本", download_count: 12480, download_url: "ipfs://QmMockPython", x: positions[0][0], y: positions[0][1], color: "#22d3ee", connections: ["medical", "voice"], manifest_json: { type: "skill", permissions: ["sandbox.execute"] } },
    { id: "medical", plugin_id: "com.jachin.medical-imaging", name: "医疗影像分析", category: "skill", description: "基于本地模型的医学影像辅助分析", download_count: 8920, download_url: "ipfs://QmMockMedical", x: positions[1][0], y: positions[1][1], color: "#22d3ee", connections: ["python", "voice"], manifest_json: { type: "skill" } },
    { id: "voice", plugin_id: "com.jachin.persona-tsundere", name: "傲娇女声 Persona", category: "persona", description: "VITS 语音包 + 性格提示词", download_count: 15620, download_url: "ipfs://QmMockVoice", x: positions[2][0], y: positions[2][1], color: "#f472b6", connections: ["python", "medical"], manifest_json: { type: "persona", execution_model: "resource_mount" } },
    { id: "docker", plugin_id: "com.jachin.docker-bridge", name: "Docker 容器桥", category: "skill", description: "将 Docker 容器作为技能调用", download_count: 5430, download_url: "ipfs://QmMockDocker", x: positions[3][0], y: positions[3][1], color: "#22d3ee", connections: ["python"], manifest_json: { type: "skill" } },
    { id: "legal", plugin_id: "com.jachin.legal-knowledge", name: "民法典知识库", category: "memory", description: "预训练向量知识库，涵盖中国民法典全文", download_count: 6780, download_url: "ipfs://QmMockLegal", x: positions[4][0], y: positions[4][1], color: "#a78bfa", connections: ["medical"], manifest_json: { type: "memory", execution_model: "resource_mount" } },
  ];
  let filtered = category ? all.filter((p) => p.category === category) : all;
  if (sort === "downloads") filtered = [...filtered].sort((a, b) => b.download_count - a.download_count);
  else if (sort === "recent") filtered = [...filtered].reverse();
  return filtered;
}
