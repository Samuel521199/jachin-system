import { NextRequest, NextResponse } from "next/server";

/**
 * POST /api/v1/forge/publish
 * Forge API - 将 React Flow 画布发布为 JMP 包
 * Body: { nodes, edges, name, plugin_id }
 */
export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const { nodes, edges, name, plugin_id } = body;

    if (!nodes || !Array.isArray(nodes) || !name || !plugin_id) {
      return NextResponse.json(
        { success: false, error: "Missing nodes, name, or plugin_id" },
        { status: 400 }
      );
    }

    // 构建 JMP manifest（符合 docs/JMP_SPEC.md）
    const manifest = buildJMPManifest({ nodes, edges, name, plugin_id });

    // TODO: 1. 将 workflow 打包成 .jmp 文件
    // TODO: 2. 上传到 IPFS/S3，获取 download_url
    // TODO: 3. 写入 plugins_registry 表，status=pending 等待审核
    const mockDownloadUrl = `ipfs://QmMock${plugin_id.replace(/\./g, "-")}`;

    return NextResponse.json({
      success: true,
      data: {
        plugin_id,
        name,
        manifest,
        download_url: mockDownloadUrl,
        status: "pending",
        message: "已提交审核，审核通过后将上架 Neural Market",
      },
    });
  } catch (e) {
    console.error("Forge publish error:", e);
    return NextResponse.json(
      { success: false, error: "Internal server error" },
      { status: 500 }
    );
  }
}

function buildJMPManifest(params: {
  nodes: unknown[];
  edges: unknown[];
  name: string;
  plugin_id: string;
}) {
  const { nodes, edges, name, plugin_id } = params;
  const permissions = extractPermissions(nodes);

  return {
    id: plugin_id,
    version: "1.0.0",
    name,
    entry: "main.py",
    permissions,
    capabilities: (nodes as { type?: string; data?: { label?: string } }[])
      .filter((n) => n.type === "action" || n.type === "llm")
      .map((n) => ({
        name: n.data?.label || "unknown",
      })),
    _workflow: { nodes, edges }, // 保留 workflow 供后续生成 main.py
  };
}

function extractPermissions(nodes: unknown[]): string[] {
  const perms: string[] = [];
  for (const n of nodes as { type?: string; data?: { label?: string } }[]) {
    if (n.type === "trigger" && n.data?.label?.includes("语音")) {
      perms.push("audio.input");
    }
    if (n.type === "action") {
      const label = (n.data?.label || "").toLowerCase();
      if (label.includes("网络") || label.includes("天气")) perms.push("internet.access");
      if (label.includes("代码") || label.includes("执行")) perms.push("sandbox.execute");
    }
  }
  return perms.length ? perms : ["sandbox.execute"];
}
