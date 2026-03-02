/**
 * ForgeCompiler - 视觉到协议的 AST 编译器
 * 战役 C：将 React Flow 画布编译为 JMP 2.0 装配清单
 *
 * 三步走：
 * 1. 拓扑解析：遍历 Edges，生成 routes（数据流向）
 * 2. 依赖与权限聚合：遍历 Nodes，收集插件依赖与权限
 * 3. 生成装配清单：输出 JMP 2.0 manifest.json
 */

export interface FlowNode {
  id: string;
  type?: string;
  data?: { label?: string; pluginId?: string; permissions?: string[] };
}

export interface FlowEdge {
  id?: string;
  source: string;
  target: string;
}

export interface Route {
  from: string;
  to: string;
}

export interface CompileResult {
  manifest: JMP20Manifest;
  routes: Route[];
  dependencies: string[];
  permissions: string[];
}

export interface JMP20Manifest {
  jmp_version: string;
  plugin_id: string;
  version: string;
  name: string;
  type: string;
  entrypoint: string;
  permissions: string[];
  capabilities: { name: string; nodeId?: string }[];
  resource_footprint?: { ram_estimate_mb: number; gpu_required: boolean };
  _workflow?: { nodes: FlowNode[]; edges: FlowEdge[] };
}

/** 节点标签 → 底层插件 ID 映射 */
const LABEL_TO_PLUGIN: Record<string, string> = {
  "语音输入": "core-vad-audio",
  "定时任务": "core-cron-trigger",
  "qwen 路由分析": "core-llm-qwen",
  "意图解析": "core-llm-intent",
  "意图分析 llm": "core-llm-intent",
  "搜索天气": "com.jachin.weather",
  "执行代码": "core-sandbox-exec",
};

/** 节点标签/类型 → 权限映射 */
function inferPermissions(node: FlowNode): string[] {
  const perms: string[] = [];
  const label = (node.data?.label ?? "").toLowerCase();
  const type = (node.type ?? "").toLowerCase();

  if (type === "trigger") {
    if (label.includes("语音")) perms.push("audio.input");
    if (label.includes("定时")) perms.push("cron.trigger");
  }
  if (type === "llm") {
    perms.push("llm.invoke");
  }
  if (type === "action") {
    if (label.includes("网络") || label.includes("天气") || label.includes("搜索"))
      perms.push("internet.access");
    if (label.includes("代码") || label.includes("执行")) perms.push("sandbox.execute");
    if (label.includes("文件")) perms.push("file.read", "file.write");
  }

  if (node.data?.permissions?.length) {
    perms.push(...node.data.permissions);
  }

  return [...new Set(perms)];
}

/** 节点标签 → 插件依赖 */
function inferPluginId(node: FlowNode): string | null {
  const label = (node.data?.label ?? "").trim();
  if (node.data?.pluginId) return node.data.pluginId;
  return LABEL_TO_PLUGIN[label] ?? LABEL_TO_PLUGIN[label.toLowerCase()] ?? null;
}

export class ForgeCompiler {
  /**
   * 编译 React Flow 画布为 JMP 2.0 装配清单
   */
  static compile(
    nodes: FlowNode[],
    edges: FlowEdge[],
    name: string,
    pluginId: string
  ): CompileResult {
    const routes = this.parseTopology(edges);
    const { dependencies, permissions } = this.aggregateDependencies(nodes);
    const manifest = this.generateManifest(
      nodes,
      edges,
      name,
      pluginId,
      permissions,
      routes
    );

    return { manifest, routes, dependencies, permissions };
  }

  /** 1. 拓扑解析：从 Edges 生成数据流向 */
  static parseTopology(edges: FlowEdge[]): Route[] {
    return edges.map((e) => ({ from: e.source, to: e.target }));
  }

  /** 2. 依赖与权限聚合 */
  static aggregateDependencies(
    nodes: FlowNode[]
  ): { dependencies: string[]; permissions: string[] } {
    const deps = new Set<string>();
    const perms = new Set<string>();

    for (const node of nodes) {
      const pluginId = inferPluginId(node);
      if (pluginId) deps.add(pluginId);
      for (const p of inferPermissions(node)) perms.add(p);
    }

    return {
      dependencies: [...deps],
      permissions: perms.size > 0 ? [...perms] : ["sandbox.execute"],
    };
  }

  /** 3. 生成 JMP 2.0 manifest */
  static generateManifest(
    nodes: FlowNode[],
    edges: FlowEdge[],
    name: string,
    pluginId: string,
    permissions: string[],
    routes: Route[]
  ): JMP20Manifest {
    const capabilities = (nodes as FlowNode[])
      .filter((n) => n.type === "action" || n.type === "llm")
      .map((n) => ({
        name: n.data?.label ?? n.id ?? "unknown",
        nodeId: n.id,
      }));

    const ramEstimate = this.estimateRam(nodes);

    return {
      jmp_version: "2.0",
      plugin_id: pluginId,
      version: "1.0.0",
      name,
      type: "skill",
      entrypoint: "main.py",
      permissions,
      capabilities,
      resource_footprint: {
        ram_estimate_mb: ramEstimate,
        gpu_required: false,
      },
      _workflow: { nodes, edges },
    };
  }

  /** 根据节点数量估算内存 */
  static estimateRam(nodes: FlowNode[]): number {
    let base = 128;
    for (const n of nodes) {
      if (n.type === "llm") base += 256;
      if (n.type === "action") base += 64;
    }
    return Math.min(base, 2048);
  }
}
