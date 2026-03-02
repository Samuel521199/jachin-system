import { NextRequest, NextResponse } from "next/server";
import { getSupabase, isSupabaseConfigured } from "@/lib/supabase";

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

    if (!isSupabaseConfigured()) {
      return NextResponse.json(
        { success: true, data: getFallbackPlugins(category, sort), meta: { total: 5, source: "fallback" } },
        { status: 200 }
      );
    }

    const sb = getSupabase()!;
    let query = sb
      .from("plugins_registry")
      .select("id, plugin_id, name, category, description, download_count, download_url, manifest_json")
      .eq("status", "approved");

    if (category) {
      query = query.eq("category", category);
    }

    if (sort === "downloads") {
      query = query.order("download_count", { ascending: false });
    } else if (sort === "recent") {
      query = query.order("created_at", { ascending: false });
    }

    const { data, error } = await query;

    if (error) {
      console.error("[plugins] Supabase error:", error);
      return NextResponse.json(
        { success: false, error: error.message },
        { status: 500 }
      );
    }

    const plugins = mapToPluginRecords(data || []);
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

function mapToPluginRecords(rows: Record<string, unknown>[]): PluginRecord[] {
  const pluginIds = rows.map((r) => r.plugin_id as string);
  const n = rows.length;
  return rows.map((row, i) => {
    const category = (row.category as string) || "skill";
    const angle = (i / n) * 2 * Math.PI - Math.PI / 2;
    const radius = 35 + (i % 3) * 8;
    return {
      id: (row.id as string) || (row.plugin_id as string),
      plugin_id: row.plugin_id as string,
      name: row.name as string,
      category,
      description: (row.description as string) || null,
      download_count: (row.download_count as number) ?? 0,
      download_url: (row.download_url as string) || "",
      manifest_json: (row.manifest_json as Record<string, unknown>) ?? null,
      x: 50 + radius * Math.cos(angle) + (i % 2 ? 5 : -5),
      y: 50 + radius * Math.sin(angle) + (i % 3 === 1 ? 3 : 0),
      color: CATEGORY_COLORS[category] || CATEGORY_COLORS.default,
      connections: pluginIds.filter((id) => id !== row.plugin_id).slice(0, 2),
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
