import { NextRequest, NextResponse } from "next/server";
import path from "path";
import fs from "fs";
import { randomUUID } from "crypto";
import { JmpPacker } from "@/core/JmpPacker";
import { ForgeCompiler } from "@/core/ForgeCompiler";
import { getSupabase, isSupabaseConfigured } from "@/lib/supabase";
import { isIpfsConfigured, uploadToIpfs } from "@/lib/ipfs";

const DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000001";

/**
 * POST /api/v1/forge/publish
 * Forge API - 将插件代码或 React Flow 画布发布为 JMP 包
 *
 * 模式 A（直接发证）: Body: { pluginId, permissions, sourceCode }
 * 模式 B（React Flow）: Body: { nodes, edges, name, plugin_id }
 */
export async function POST(request: NextRequest) {
  try {
    const body = await request.json();

    // 模式 A：pluginId + permissions + sourceCode → 直接生成 .jmp
    if (body.pluginId && body.sourceCode !== undefined) {
      return await handleDirectPublish(body);
    }

    // 模式 B：React Flow 画布 → manifest 构建（后续对接 JmpPacker）
    if (body.nodes && body.plugin_id) {
      return await handleWorkflowPublish(body);
    }

    return NextResponse.json(
      { success: false, error: "Missing pluginId+sourceCode or nodes+plugin_id" },
      { status: 400 }
    );
  } catch (e) {
    console.error("Forge publish error:", e);
    return NextResponse.json(
      { success: false, error: (e as Error).message },
      { status: 500 }
    );
  }
}

/** 模式 A：直接发证，生成真实 .jmp 文件 */
async function handleDirectPublish(body: {
  pluginId: string;
  permissions?: string[];
  sourceCode: string | Buffer;
  target_instance_id?: string;
}) {
  const { pluginId, permissions = ["sandbox.execute"], sourceCode, target_instance_id } = body;

  const privateKey = process.env.JACHIN_PRIVATE_KEY;
  if (!privateKey) {
    throw new Error("服务器未配置 JACHIN_PRIVATE_KEY");
  }

  const tmpDir = path.join(process.cwd(), "tmp");
  const payloadDir = path.join(tmpDir, `payload-${pluginId}`);
  const outputPath = path.join(tmpDir, `${pluginId}.jmp`);

  fs.mkdirSync(payloadDir, { recursive: true });
  const codeBuffer =
    typeof sourceCode === "string"
      ? Buffer.from(sourceCode, "utf8")
      : Buffer.isBuffer(sourceCode)
        ? sourceCode
        : Buffer.from(String(sourceCode));
  fs.writeFileSync(path.join(payloadDir, "module.wasm"), codeBuffer);

  const packer = new JmpPacker(privateKey);
  await packer.pack(pluginId, payloadDir, outputPath, permissions);

  fs.rmSync(payloadDir, { recursive: true, force: true });

  let downloadUrl = `https://nexus.jachin/downloads/${pluginId}.jmp`;
  if (isIpfsConfigured()) {
    const fileBuffer = fs.readFileSync(outputPath);
    const ipfsResult = await uploadToIpfs(
      fileBuffer,
      `${pluginId.replace(/\./g, "-")}_${Date.now()}.jmp`,
      "application/zip"
    );
    if (ipfsResult) downloadUrl = ipfsResult.url;
  }
  if (!downloadUrl.startsWith("ipfs://") && isSupabaseConfigured()) {
    const url = await uploadJmpToStorage(outputPath, pluginId);
    if (url) downloadUrl = url;
  }
  if (target_instance_id && isSupabaseConfigured()) {
    await insertDeployCommand(downloadUrl, pluginId, target_instance_id);
  }
  try {
    fs.rmSync(outputPath, { force: true });
  } catch {
    /* ignore */
  }

  return NextResponse.json({
    success: true,
    message: "武器锻造并签名成功！",
    jmp_url: downloadUrl,
    plugin_id: pluginId,
  });
}

/** 模式 B：React Flow 画布 → ForgeCompiler → JmpPacker 签名打包 */
async function handleWorkflowPublish(body: {
  nodes: unknown[];
  edges?: unknown[];
  name: string;
  plugin_id: string;
  target_instance_id?: string;
}) {
  const { nodes, edges, name, plugin_id, target_instance_id } = body;

  if (!nodes || !Array.isArray(nodes) || !name || !plugin_id) {
    return NextResponse.json(
      { success: false, error: "Missing nodes, name, or plugin_id" },
      { status: 400 }
    );
  }

  const privateKey = process.env.JACHIN_PRIVATE_KEY;
  if (!privateKey) {
    return NextResponse.json(
      { success: false, error: "服务器未配置 JACHIN_PRIVATE_KEY，无法签名打包" },
      { status: 500 }
    );
  }

  const typedNodes = nodes as { id: string; type?: string; data?: { label?: string } }[];
  const typedEdges = (edges ?? []) as { source: string; target: string }[];

  const { manifest, routes, dependencies, permissions } = ForgeCompiler.compile(
    typedNodes,
    typedEdges,
    name,
    plugin_id
  );

  const tmpDir = path.join(process.cwd(), "tmp");
  const payloadDir = path.join(tmpDir, `payload-${plugin_id}`);
  const outputPath = path.join(tmpDir, `${plugin_id}.jmp`);

  fs.mkdirSync(payloadDir, { recursive: true });

  const workflowPayload = {
    plugin_id,
    routes,
    dependencies,
    nodes: typedNodes,
    edges: typedEdges,
  };
  fs.writeFileSync(
    path.join(payloadDir, "workflow.json"),
    JSON.stringify(workflowPayload, null, 2),
    "utf8"
  );

  const mainPy = generateWorkflowMainPy(workflowPayload);
  fs.writeFileSync(path.join(payloadDir, "main.py"), mainPy, "utf8");

  const packer = new JmpPacker(privateKey);
  await packer.pack(plugin_id, payloadDir, outputPath, permissions, {
    name: manifest.name,
    entrypoint: "main.py",
    capabilities: manifest.capabilities,
    type: manifest.type,
    resource_footprint: manifest.resource_footprint,
    _workflow: manifest._workflow,
  });

  fs.rmSync(payloadDir, { recursive: true, force: true });

  let downloadUrl = `https://nexus.jachin/downloads/${plugin_id}.jmp`;
  if (isIpfsConfigured()) {
    const fileBuffer = fs.readFileSync(outputPath);
    const ipfsResult = await uploadToIpfs(
      fileBuffer,
      `${plugin_id.replace(/\./g, "-")}_${Date.now()}.jmp`,
      "application/zip"
    );
    if (ipfsResult) downloadUrl = ipfsResult.url;
  }
  if (!downloadUrl.startsWith("ipfs://") && isSupabaseConfigured()) {
    const url = await uploadJmpToStorage(outputPath, plugin_id);
    if (url) downloadUrl = url;
  }
  if (target_instance_id && isSupabaseConfigured()) {
    await insertDeployCommand(downloadUrl, plugin_id, target_instance_id);
  }
  try {
    fs.rmSync(outputPath, { force: true });
  } catch {
    /* ignore */
  }

  return NextResponse.json({
    success: true,
    message: "武器锻造并签名成功！画布已编译为 JMP 2.0 装配清单。",
    jmp_url: downloadUrl,
    plugin_id,
    manifest: {
      ...manifest,
      permissions,
      dependencies,
    },
  });
}

function generateWorkflowMainPy(workflow: {
  routes: { from: string; to: string }[];
  dependencies: string[];
  nodes: unknown[];
  edges: unknown[];
}): string {
  const raw = JSON.stringify(workflow);
  const escaped = raw.replace(/\\/g, "\\\\").replace(/"/g, '\\"').replace(/\n/g, "\\n");
  return `# Forge 工作流 Agent - 由 React Flow 画布自动生成
# JMP 2.0 装配清单运行时

import json

WORKFLOW = "${escaped}"

def load_workflow():
    return json.loads(WORKFLOW)

def setup(agent_context: dict) -> dict:
    """标准插件入口 - 供 Commander 路由调用；同时注册到 Event Bus 实现响应式唤醒"""
    wf = load_workflow()
    try:
        from core.plugin.workflow_runner import register_workflow_to_event_bus
        plugin_id = agent_context.get("plugin_id") or wf.get("plugin_id") or "workflow"
        register_workflow_to_event_bus(plugin_id, wf)
    except Exception:
        pass  # Event Bus 不可用时静默跳过
    return {
        "capabilities": [
            {
                "name": "workflow_execute",
                "description": "执行 Forge 工作流",
                "handler": handle_workflow,
                "parameters": {"type": "object", "properties": {}, "required": []},
            }
        ],
        "intent_keywords": ["工作流", "执行"],
        "_workflow": wf,
    }

def handle_workflow(params: dict) -> dict:
    """工作流执行 - 由 WorkflowRunner 驱动 DAG 调度"""
    from core.plugin.workflow_runner import WorkflowRunner
    wf = load_workflow()
    runner = WorkflowRunner(wf)
    result = runner.run(params)
    if result.get("success"):
        data = result.get("data", result.get("outputs", {}))
        text = data.get("text", str(data)) if isinstance(data, dict) else str(data)
        return {"success": True, "text": text, "tts": text, "data": data}
    return {
        "success": False,
        "text": result.get("error", "工作流执行失败"),
        "tts": result.get("error", "工作流执行失败"),
        "error": result.get("error"),
    }
`;
}

/** 上传 .jmp 至 Supabase Storage (jmp-packages bucket) */
async function uploadJmpToStorage(
  outputPath: string,
  pluginId: string
): Promise<string | null> {
  const sb = getSupabase();
  if (!sb) return null;

  const fileBuffer = fs.readFileSync(outputPath);
  const fileName = `${pluginId.replace(/\./g, "-")}_${Date.now()}.jmp`;

  const { error: uploadError } = await sb.storage
    .from("jmp-packages")
    .upload(fileName, fileBuffer, {
      contentType: "application/zip",
      upsert: true,
    });

  if (uploadError) {
    console.error("☁️ 上传存储失败:", uploadError.message);
    return null;
  }

  const { data: publicUrlData } = sb.storage
    .from("jmp-packages")
    .getPublicUrl(fileName);

  console.log(`✅ 武器包已上传至云端弹药库: ${publicUrlData.publicUrl}`);
  return publicUrlData.publicUrl;
}

/** 向目标边缘智能体下发部署指令 (deploy_commands) */
async function insertDeployCommand(
  downloadUrl: string,
  pluginId: string,
  targetInstanceId: string
): Promise<void> {
  const sb = getSupabase();
  if (!sb) return;

  const tempToken = randomUUID();
  const tokenExpiresAt = new Date(Date.now() + 15 * 60 * 1000);

  const { error } = await sb.from("deploy_commands").insert({
    user_id: DEFAULT_USER_ID,
    layer2_instance_id: targetInstanceId,
    resource_type: "plugin",
    resource_id: randomUUID(),
    plugin_id: pluginId,
    download_url: downloadUrl,
    temp_token: tempToken,
    token_expires_at: tokenExpiresAt.toISOString(),
    status: "pending",
  });

  if (error) {
    console.error("📡 部署指令下发失败:", error.message);
    throw new Error(`指令下发失败: ${error.message}`);
  }
  console.log(`📡 已向边缘智能体 [${targetInstanceId}] 下发部署指令，plugin_id=${pluginId}`);
}
