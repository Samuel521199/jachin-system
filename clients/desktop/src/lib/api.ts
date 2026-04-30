/**
 * API Client - 与后端通信
 *
 * V2: Dapr 已废弃，统一直连后端 API。
 */

/** 后端 base URL（L2） */
export const BACKEND_URL = import.meta.env.VITE_BACKEND_URL || "http://localhost:18888";

/** L3 技能 API 默认端口（与 l3_node/http_server.py 端口回退一致） */
const L3_SKILLS_PORTS = [18991, 18990, 18992, 18993, 18994, 18995, 18996, 18997, 18998, 18999];

/**
 * 可选构建时变量 **VITE_L3_SKILLS_URL**：打包进前端的 L3 HTTP 根（如 `http://127.0.0.1:18991` 或海外 `https://l3.example.com`）。
 * **不必设置**：未设置时对多端口 **并行** 短探测 + 成功后 **内存缓存**（约 90s），避免串行扫端口拖死主流程。
 */
const L3_SKILLS_BASE = import.meta.env.VITE_L3_SKILLS_URL || "http://127.0.0.1";

/** 开发模式下使用 Vite 代理 /l3 -> L3，避免跨域 Failed to fetch */
const L3_DEV_PROXY = import.meta.env.DEV ? "/l3" : "";

/** 单端口探测超时（毫秒）；多端口 **并行** 发起，总等待约本值量级而非端口数×本值 */
const L3_PROBE_FETCH_MS = 2200;

/** 探测到的 L3 base 在内存中复用时长，减轻巡检页定时轮询等对主线程的压力 */
const L3_BASE_CACHE_MS = 90_000;

let _l3BaseUrlCache: { url: string; until: number } | null = null;

async function fetchL3ProbeOk(url: string): Promise<boolean> {
  const ac = new AbortController();
  const t = setTimeout(() => ac.abort(), L3_PROBE_FETCH_MS);
  try {
    const r = await fetch(url, {
      method: "GET",
      headers: { Accept: "application/json" },
      signal: ac.signal,
    });
    return r.ok;
  } catch {
    return false;
  } finally {
    clearTimeout(t);
  }
}

/** 保存 API Key 到后端（持久化到 ~/.jachin/.qwen_api_key，覆盖 .env） */
export async function saveApiKey(qwenApiKey: string | null): Promise<{ ok: boolean; message: string }> {
  const res = await fetch(`${BACKEND_URL}/api/v3/config/apikey`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ qwen_api_key: qwenApiKey || null }),
  });
  const data = await res.json().catch(() => ({}));
  return { ok: res.ok, message: data?.message ?? (res.ok ? "已保存" : "保存失败") };
}
export interface ChatMessage {
  role: "user" | "assistant" | "system";
  content: string;
  timestamp?: number;
}

export interface ChatRequest {
  message: string;
}

export interface ChatResponse {
  reply: string;
}

export interface DeviceStatus {
  deviceId: string;
  name: string;
  status: "online" | "offline" | "error";
  capabilities: string[];
  lastSeen?: number;
}

/** 技能信息（与后端 SkillInfo 一致） */
export interface SkillInfo {
  skill_id: string;
  name: string;
  version: string;
  description?: string;
  status: string;
  capabilities: Array<{ name?: string; description?: string; [k: string]: unknown }>;
  permissions?: Array<{ id: string; label: string }>;
  /** 执行次数（来自 /api/v3/skills 或 /api/v3/skills/stats） */
  execution_count?: number;
  /** 上次执行时间 */
  last_executed_at?: string | null;
  /** L2 inventory 目录名，卸载时使用 */
  item_id?: string;
}

/** V2: 统一直连后端 API（Dapr 已废弃） */
async function invokeBackend<T>(
  method: string,
  data?: any,
  httpVerb: "GET" | "POST" | "PUT" | "PATCH" | "DELETE" = "POST"
): Promise<T> {
  return invokeDirectly<T>(method, data, httpVerb);
}

async function invokeDirectly<T>(
  method: string,
  data?: any,
  httpVerb: "GET" | "POST" | "PUT" | "PATCH" | "DELETE" = "POST"
): Promise<T> {
  // 直接调用后端 API
  const normalizedMethod = method.startsWith("/") ? method : `/${method}`;
  const url = `${BACKEND_URL}${normalizedMethod}`;

  // 调试日志
  console.log(`[Direct] Calling: ${url}`);
  console.log(`[Direct] Method: ${httpVerb}, Data:`, data);

  const options: RequestInit = {
    method: httpVerb,
    headers: {
      "Content-Type": "application/json",
    },
  };

  if (data && httpVerb !== "GET") {
    options.body = JSON.stringify(data);
  }

  try {
    const response = await fetch(url, options);

    if (!response.ok) {
      const errorText = await response.text();
      console.error(`[Direct] Error ${response.status}:`, errorText);
      throw new Error(`HTTP ${response.status}: ${errorText || response.statusText}`);
    }

    const result = await response.json();
    console.log(`[Direct] Success:`, result);
    return result;
  } catch (error) {
    if (error instanceof TypeError && error.message.includes("fetch")) {
      throw new Error(
        `无法连接到后端服务 (${BACKEND_URL})。\n\n` +
        "请确保：\n" +
        "1. 后端服务已启动（运行 启动后端.bat 或 .\\scripts\\start.ps1）\n" +
        "2. 后端服务运行在端口 18888\n\n" +
        "检查命令：\n" +
        `curl ${BACKEND_URL}/health`
      );
    }
    throw error;
  }
}

/**
 * 发送聊天消息
 */
export async function sendChatMessage(message: string): Promise<ChatResponse> {
  return invokeBackend<ChatResponse>("/api/chat", { message });
}

/**
 * 流式聊天（SSE），逐字返回，供打字机效果。
 * V2: 统一直连后端（Dapr 已废弃）。
 */
export async function streamChatMessage(
  message: string,
  onChunk: (text: string) => void,
  opts?: { signal?: AbortSignal }
): Promise<string> {
  const url = `${BACKEND_URL}/api/v2/chat/text`;
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json; charset=utf-8" },
    body: JSON.stringify({
      messages: [{ role: "user", content: message }],
      stream: true,
    }),
    signal: opts?.signal,
  });
  if (!res.ok) throw new Error(`Stream chat failed: ${res.status}`);
  const reader = res.body?.getReader();
  if (!reader) throw new Error("No response body");
  const decoder = new TextDecoder("utf-8");
  let fullText = "";
  const buf: string[] = [];
  while (true) {
    if (opts?.signal?.aborted) {
      try {
        await reader.cancel();
      } catch {
        /* noop */
      }
      break;
    }
    const { done, value } = await reader.read();
    if (done) break;
    buf.push(decoder.decode(value, { stream: true }));
    const joined = buf.join("");
    const lines = joined.split("\n");
    buf.length = 0;
    if (lines.length > 1) buf.push(lines.pop() || "");
    for (const line of lines) {
      if (line.startsWith("data: ")) {
        const data = line.slice(6).trim();
        if (data === "[DONE]") continue;
        if (data) {
          fullText += data;
          onChunk(data);
        }
      }
    }
  }
  if (opts?.signal?.aborted) {
    return fullText;
  }
  if (!fullText) {
    throw new Error("Stream returned no data");
  }
  return fullText;
}

/**
 * 检查后端健康状态
 */
export async function checkHealth(): Promise<{ status: string }> {
  return invokeBackend<{ status: string }>("/health", undefined, "GET");
}
/** 本地/当前 LLM 可用性（直连后端，不经过 Dapr） */
export interface LLMStatus {
  available: boolean;
  provider: string | null;
  model: string | null;
}
export async function checkLocalModelAvailability(): Promise<LLMStatus> {
  try {
    const res = await fetch(`${BACKEND_URL}/api/v2/chat/llm-status`, { method: "GET" });
    if (!res.ok) return { available: false, provider: null, model: null };
    const data = await res.json();
    return {
      available: Boolean(data?.available),
      provider: data?.provider ?? null,
      model: data?.model ?? null,
    };
  } catch {
    return { available: false, provider: null, model: null };
  }
}

/** 集群统计（与后端 ClusterStats 一致） */
export interface ClusterStats {
  nodes?: { online?: number; offline?: number; total?: number };
  tasks?: { pending?: number; running?: number; completed?: number; failed?: number; total?: number };
  resources?: Record<string, unknown>;
  utilization?: Record<string, unknown>;
}

/**
 * 获取集群统计（GET /api/v3/cluster/stats）
 * 404 时返回单机占位（后端可能未实现 cluster 路由）
 */
export async function getClusterStats(): Promise<ClusterStats> {
  try {
    return await invokeBackend<ClusterStats>("/api/v3/cluster/stats", undefined, "GET");
  } catch {
    return {
      nodes: { online: 1, offline: 0, total: 1 },
      tasks: { pending: 0, running: 0, completed: 0, failed: 0, total: 0 },
      resources: {},
      utilization: {},
    };
  }
}

/** 集群节点信息 */
export interface ClusterNodeInfo {
  node_id: string;
  node_type: string;
  host: string;
  port: number;
  status: string;
  resources?: Record<string, unknown>;
}

/** 集群任务信息 */
export interface ClusterTaskInfo {
  task_id: string;
  task_type: string;
  status: string;
  skill_id?: string;
  capability_name?: string;
  worker_node?: string;
  created_at?: string;
  completed_at?: string;
}

/**
 * 获取集群节点列表（GET /api/v3/cluster/nodes）
 */
export async function getClusterNodes(): Promise<ClusterNodeInfo[]> {
  return invokeBackend<ClusterNodeInfo[]>("/api/v3/cluster/nodes", undefined, "GET");
}

/**
 * 获取推理策略（GET /api/v3/inference/strategy）
 */
export async function getInferenceStrategy(): Promise<{ mode: string; label: string }> {
  return invokeBackend<{ mode: string; label: string }>("/api/v3/inference/strategy", undefined, "GET");
}

/**
 * 设置推理策略（POST /api/v3/inference/strategy）
 */
export async function setInferenceStrategy(mode: string): Promise<{ ok: boolean; mode: string; label: string }> {
  return invokeBackend<{ ok: boolean; mode: string; label: string }>("/api/v3/inference/strategy", { mode }, "POST");
}

/**
 * 获取集群任务列表（GET /api/v3/cluster/tasks）
 */
export async function getClusterTasks(param?: {
  status?: string;
  skill_id?: string;
  limit?: number;
}): Promise<ClusterTaskInfo[]> {
  const params = new URLSearchParams();
  if (param?.status) params.set("status", param.status);
  if (param?.skill_id) params.set("skill_id", param.skill_id);
  if (param?.limit) params.set("limit", String(param.limit));
  const q = params.toString();
  return invokeBackend<ClusterTaskInfo[]>(
    `/api/v3/cluster/tasks${q ? `?${q}` : ""}`,
    undefined,
    "GET"
  );
}

/** 配置响应（供 Horizon 环境/模型显示） */
export interface ConfigResponse {
  environment: string;
  model_name: string;
  cluster_mode?: string;
  llm_provider?: string;
}

/**
 * 获取配置（GET /api/v3/config）
 */
export async function getConfig(): Promise<ConfigResponse> {
  return invokeBackend<ConfigResponse>("/api/v3/config", undefined, "GET");
}

/**
 * 获取 LLM 上下文 Token 占用（GET /api/v3/llm/context，供 ModelController）
 */
export async function getLlmContext(): Promise<{ used: number; max: number }> {
  return invokeBackend<{ used: number; max: number }>("/api/v3/llm/context", undefined, "GET");
}

/**
 * 重置上下文 Token 计数（新会话时调用）
 */
export async function resetLlmContext(): Promise<{ ok: boolean }> {
  return invokeBackend<{ ok: boolean }>("/api/v3/llm/context/reset", undefined, "POST");
}

/** GPU 统计项 */
export interface GpuStatsItem {
  index: number;
  name: string;
  memory_total_mb: number;
  memory_used_mb: number;
  memory_free_mb: number;
  utilization_gpu: number | null;
  utilization_memory: number | null;
  temperature_c: number | null;
}

/**
 * 获取 GPU 统计（GET /api/v3/gpu/stats，温度、利用率、显存）
 */
export async function getGpuStats(): Promise<{
  gpus: GpuStatsItem[];
  message?: string | null;
}> {
  return invokeBackend("/api/v3/gpu/stats", undefined, "GET");
}

/**
 * 获取记忆数量（宿主记忆在 L3 Memory Nexus；本地控制台未接 palace 统计时返回 0）
 */
export async function getMemoryCount(): Promise<{ count: number }> {
  return { count: 0 };
}

/**
 * 获取最近日志（GET /api/v3/logs/recent，供思维流）
 */
export async function getLogsRecent(limit = 20, naturalize = true): Promise<{ lines: string[] }> {
  return invokeBackend<{ lines: string[] }>(
    `/api/v3/logs/recent?limit=${limit}&naturalize=${naturalize}`,
    undefined,
    "GET"
  );
}

/** 建议卡片项 */
export interface SuggestionItem {
  id: string;
  text: string;
  action: string;
  type?: string;
}

/**
 * 获取建议卡片（GET /api/v3/suggestions）
 */
export async function getSuggestions(): Promise<{ items: SuggestionItem[] }> {
  return invokeBackend<{ items: SuggestionItem[] }>("/api/v3/suggestions", undefined, "GET");
}

/**
 * 执行建议（POST /api/v3/suggestions/{id}/execute）
 */
export async function executeSuggestion(
  suggestionId: string,
  action: string = "执行"
): Promise<{ ok: boolean; message: string }> {
  return invokeBackend(`/api/v3/suggestions/${encodeURIComponent(suggestionId)}/execute`, {
    action,
  });
}

/** 记忆搜索结果 */
export interface MemorySearchResult {
  id: string;
  text: string;
  score: number;
  metadata?: Record<string, unknown>;
}

/**
 * 记忆搜索（宿主记忆由 L3 Agent 侧 `core:local_memory_search` / Memory Nexus；此处占位未接 HTTP）
 */
export async function searchMemory(
  _q: string
): Promise<{ results: MemorySearchResult[]; message?: string }> {
  return { results: [], message: "记忆检索由 L3 Memory Nexus（对话内工具）完成" };
}

/** 模型项 */
export interface ModelItem {
  id: string;
  name: string;
  description?: string;
}

/**
 * 获取模型列表（GET /api/v3/models）
 */
export async function getModels(): Promise<{ models: ModelItem[]; current: string }> {
  return invokeBackend<{ models: ModelItem[]; current: string }>(
    "/api/v3/models",
    undefined,
    "GET"
  );
}

/**
 * 切换当前模型（POST /api/v3/models/current）
 */
export async function setCurrentModel(
  modelId: string
): Promise<{ ok: boolean; current: string }> {
  return invokeBackend("/api/v3/models/current", { model_id: modelId });
}

/** 日历条目 */
export interface CalendarItem {
  id: string;
  title: string;
  description?: string;
  item_type: "event" | "reminder" | "todo";
  start_at: string;
  end_at?: string;
  recurrence: string;
  recurrence_interval: number;
  is_done: boolean;
  created_at: string;
  updated_at: string;
}

/**
 * 获取日历条目（GET /api/v3/calendar/items）
 */
export async function getCalendarItems(param?: {
  item_type?: "event" | "reminder" | "todo";
  include_done?: boolean;
  days?: number;
}): Promise<{ items: CalendarItem[] }> {
  const params = new URLSearchParams();
  if (param?.item_type) params.set("item_type", param.item_type);
  if (param?.include_done) params.set("include_done", "true");
  if (param?.days) params.set("days", String(param.days));
  const q = params.toString();
  return invokeBackend<{ items: CalendarItem[] }>(
    `/api/v3/calendar/items${q ? `?${q}` : ""}`,
    undefined,
    "GET"
  );
}

/**
 * 创建日历条目（POST /api/v3/calendar/items）
 */
export async function createCalendarItem(body: {
  title: string;
  description?: string;
  item_type?: "event" | "reminder" | "todo";
  start_at: string;
  end_at?: string;
  recurrence?: string;
  recurrence_interval?: number;
}): Promise<CalendarItem> {
  return invokeBackend<CalendarItem>("/api/v3/calendar/items", {
    title: body.title,
    description: body.description,
    item_type: body.item_type ?? "reminder",
    start_at: body.start_at,
    end_at: body.end_at,
    recurrence: body.recurrence ?? "none",
    recurrence_interval: body.recurrence_interval ?? 1,
  });
}

/**
 * 更新日历条目（PATCH /api/v3/calendar/items/{id}）
 */
export async function updateCalendarItem(
  itemId: string,
  updates: Partial<{ title: string; is_done: boolean; start_at: string }>
): Promise<CalendarItem> {
  return invokeBackend<CalendarItem>(
    `/api/v3/calendar/items/${encodeURIComponent(itemId)}`,
    updates,
    "PATCH"
  );
}

/**
 * 删除日历条目（DELETE /api/v3/calendar/items/{id}）
 */
export async function deleteCalendarItem(itemId: string): Promise<{ ok: boolean }> {
  return invokeBackend<{ ok: boolean }>(
    `/api/v3/calendar/items/${encodeURIComponent(itemId)}`,
    undefined,
    "DELETE"
  );
}

/**
 * 批量删除记忆（控制台未接 Chroma 管理 API 时为占位）
 */
export async function batchDeleteMemory(
  _memoryIds: string[]
): Promise<{ ok: boolean; deleted?: number; message?: string }> {
  return { ok: false, deleted: 0, message: "宿主记忆在 L3 Memory Nexus；批量删除请走运维/后续 API" };
}

/**
 * 删除记忆（控制台未接 Chroma 管理 API 时为占位）
 */
export async function deleteMemory(_memoryId: string): Promise<{ ok: boolean; message?: string }> {
  return { ok: false, message: "宿主记忆在 L3 Memory Nexus；单条删除请走运维/后续 API" };
}

/**
 * 获取设备列表（GET /api/v2/devices）
 * @param onlineOnly 默认 true，仅返回在线设备；false 时返回全部已审批设备（含离线）
 */
export async function getDevices(onlineOnly = true): Promise<DeviceStatus[]> {
  const q = `online_only=${onlineOnly}`;
  const res = await invokeBackend<{ devices: Array<{
    device_id: string;
    device_type?: string;
    location?: string;
    capabilities: Array<{ name: string }>;
    metadata?: Record<string, unknown>;
    timestamp?: number;
    online: boolean;
  }> }>(`/api/v2/devices?${q}`, undefined, "GET");
  const list = res?.devices ?? [];
  return list.map((d) => ({
    deviceId: d.device_id,
    name: (d.metadata?.name as string) ?? d.device_id ?? d.device_type ?? "未知设备",
    status: d.online ? "online" : "offline",
    capabilities: (d.capabilities ?? []).map((c) => c.name),
    lastSeen: d.timestamp,
  }));
}

/**
 * 控制设备（POST /api/devices/control 或通过 Dapr 转发）
 */
export async function controlDevice(
  deviceId: string,
  action: string,
  params?: Record<string, unknown>
): Promise<unknown> {
  return invokeBackend("/api/devices/control", {
    deviceId,
    action,
    params,
  });
}

/**
 * 技能 API 调用（执行在 L3 进行）
 * 若默认端口 18991 被占用，L3 会绑定 18990/18992 等，此处依次尝试
 */
async function invokeL3Skills<T>(
  method: string,
  data?: unknown,
  httpVerb: "GET" | "POST" | "PUT" | "DELETE" = "GET"
): Promise<T> {
  const path = method.startsWith("/") ? method : `/${method}`;
  const options: RequestInit = {
    method: httpVerb,
    headers: { "Content-Type": "application/json" },
  };
  if (data && !["GET", "DELETE"].includes(httpVerb)) options.body = JSON.stringify(data);

  // 若 VITE_L3_SKILLS_URL 为完整 URL（含端口），则只试该地址
  const envUrl = import.meta.env.VITE_L3_SKILLS_URL;
  if (envUrl && envUrl.includes("://") && /\d{4,5}/.test(envUrl)) {
    const res = await fetch(`${envUrl.replace(/\/$/, "")}${path}`, options);
    if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
    return res.json();
  }

  // 开发模式：优先走 Vite 代理，避免跨域
  if (L3_DEV_PROXY) {
    try {
      const res = await fetch(`${L3_DEV_PROXY}${path}`, options);
      if (res.ok) return res.json();
      // 404：对端可能是旧 L3 或未注册路由，继续尝试直连多端口
      if (res.status === 404) {
        /* fall through to direct */
      } else {
        throw new Error(`HTTP ${res.status}: ${await res.text()}`);
      }
    } catch (e) {
      if ((e as Error)?.message?.includes("Failed to fetch") || (e as Error)?.message?.includes("NetworkError")) {
        /* fall through to direct */
      } else {
        throw e;
      }
    }
  }

  // 否则依次尝试 18991/18990 等（与 l3_node/http_server.py 端口回退一致）
  let lastErr: Error | null = null;
  for (const port of L3_SKILLS_PORTS) {
    try {
      const url = `${L3_SKILLS_BASE.replace(/:\d+$/, "")}:${port}${path}`;
      const res = await fetch(url, options);
      if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
      return res.json();
    } catch (e) {
      lastErr = e instanceof Error ? e : new Error(String(e));
      const msg = (e as Error)?.message ?? "";
      if (msg.includes("Failed to fetch") || msg.includes("NetworkError")) {
        continue;
      }
      // 404：该端口可能不是本仓库 L3 或路由未注册，尝试下一端口
      if (msg.includes("HTTP 404")) {
        continue;
      }
      throw e;
    }
  }
  throw lastErr ?? new Error("L3 技能 API 不可达");
}

/** 与 L3 进程环境变量 JACHIN_SAFETY_LOCK_ADMIN_TOKEN 对应；由用户在控制台输入，勿硬编码进仓库 */
export const SAFETY_LOCK_TOKEN_HEADER = "X-Jachin-Safety-Lock-Token";

export interface SafetyLockPendingItem {
  pending_id: string;
  body: string;
  source?: string;
  tags?: unknown;
  created_at?: string;
}

export interface SafetyLockMutationResult {
  ok: boolean;
  pending_id?: string;
  entry_id?: string;
  path?: string;
  message?: string;
  error?: string;
}

/** 带额外请求头的 L3 调用（多端口回退，与 invokeL3Skills 一致） */
async function invokeL3WithExtraHeaders(
  path: string,
  init: RequestInit,
  extraHeaders: Record<string, string>
): Promise<Response> {
  const baseHeaders: Record<string, string> = {
    ...(init.headers as Record<string, string> | undefined),
    ...extraHeaders,
  };
  const merged: RequestInit = { ...init, headers: baseHeaders };
  const p = path.startsWith("/") ? path : `/${path}`;
  const envUrl = import.meta.env.VITE_L3_SKILLS_URL;
  if (envUrl && envUrl.includes("://") && /\d{4,5}/.test(envUrl)) {
    return fetch(`${envUrl.replace(/\/$/, "")}${p}`, merged);
  }
  if (L3_DEV_PROXY) {
    try {
      return await fetch(`${L3_DEV_PROXY}${p}`, merged);
    } catch (e) {
      if (
        (e as Error)?.message?.includes("Failed to fetch") ||
        (e as Error)?.message?.includes("NetworkError")
      ) {
        /* fall through */
      } else {
        throw e;
      }
    }
  }
  let lastErr: Error | null = null;
  for (const port of L3_SKILLS_PORTS) {
    try {
      const url = `${L3_SKILLS_BASE.replace(/:\d+$/, "")}:${port}${p}`;
      return await fetch(url, merged);
    } catch (e) {
      lastErr = e instanceof Error ? e : new Error(String(e));
      if (
        (e as Error)?.message?.includes("Failed to fetch") ||
        (e as Error)?.message?.includes("NetworkError")
      ) {
        continue;
      }
      throw e;
    }
  }
  throw lastErr ?? new Error("L3 技能 API 不可达");
}

async function readL3ResponseJson(res: Response): Promise<Record<string, unknown>> {
  const text = await res.text();
  try {
    return JSON.parse(text) as Record<string, unknown>;
  } catch {
    throw new Error(text || `HTTP ${res.status}`);
  }
}

/** 列出安全锁待审批条目（需管理员密钥） */
export async function fetchSafetyLockPending(adminToken: string): Promise<{
  ok: boolean;
  count?: number;
  items?: SafetyLockPendingItem[];
  error?: string;
  message?: string;
}> {
  const tok = (adminToken || "").trim();
  const res = await invokeL3WithExtraHeaders(
    "/api/v3/safety-lock/pending",
    { method: "GET" },
    { [SAFETY_LOCK_TOKEN_HEADER]: tok }
  );
  const data = await readL3ResponseJson(res);
  if (!res.ok) {
    return {
      ok: false,
      error: String(data.error ?? "request_failed"),
      message: typeof data.message === "string" ? data.message : undefined,
    };
  }
  return {
    ok: data.ok !== false,
    count: typeof data.count === "number" ? data.count : 0,
    items: Array.isArray(data.items) ? (data.items as SafetyLockPendingItem[]) : [],
  };
}

/** 审批通过：写入 ~/.jachin/JACHIN_SAFETY_LOCK.md */
export async function approveSafetyLockPending(
  adminToken: string,
  pendingId: string
): Promise<SafetyLockMutationResult> {
  const res = await invokeL3WithExtraHeaders(
    "/api/v3/safety-lock/approve",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pending_id: pendingId }),
    },
    { [SAFETY_LOCK_TOKEN_HEADER]: (adminToken || "").trim() }
  );
  const data = (await readL3ResponseJson(res)) as unknown as SafetyLockMutationResult;
  if (!res.ok) {
    return { ok: false, error: data.error ?? "request_failed", message: data.message };
  }
  return data;
}

/** 拒绝待审批条目（仅删除 pending 文件） */
export async function rejectSafetyLockPending(
  adminToken: string,
  pendingId: string
): Promise<SafetyLockMutationResult> {
  const res = await invokeL3WithExtraHeaders(
    "/api/v3/safety-lock/reject",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pending_id: pendingId }),
    },
    { [SAFETY_LOCK_TOKEN_HEADER]: (adminToken || "").trim() }
  );
  const data = (await readL3ResponseJson(res)) as unknown as SafetyLockMutationResult;
  if (!res.ok) {
    return { ok: false, error: data.error ?? "request_failed", message: data.message };
  }
  return data;
}

/** BI 等 L3 专用意图：在 L2 兜底前优先尝试 L3 agent/run（当 Sensory WebSocket 未连接时） */
const BI_INTENT_REGEX = /BI\s*分析|bi\s*分析|帮我开始.*BI|今天的BI分析|开始BI分析|执行BI分析/i;

export async function tryL3AgentForIntent(userInput: string): Promise<string | null> {
  const t = (userInput || "").trim();
  if (!t || !BI_INTENT_REGEX.test(t)) return null;
  const path = "/api/v3/agent/run";
  const options: RequestInit = {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_input: userInput }),
  };
  const envUrl = import.meta.env.VITE_L3_SKILLS_URL;
  if (envUrl && envUrl.includes("://") && /\d{4,5}/.test(envUrl)) {
    try {
      const base = envUrl.replace(/\/$/, "");
      const res = await fetch(`${base}${path}`, options);
      if (!res.ok) return null;
      const data = await res.json();
      return data?.answer ?? data?.error ?? null;
    } catch {
      return null;
    }
  }
  if (L3_DEV_PROXY) {
    try {
      const res = await fetch(`${L3_DEV_PROXY}${path}`, options);
      if (!res.ok) return null;
      const data = await res.json();
      return data?.answer ?? data?.error ?? null;
    } catch {
      /* fall through */
    }
  }
  for (const port of L3_SKILLS_PORTS) {
    try {
      const base = L3_SKILLS_BASE.replace(/:\d+$/, "").replace(/\/$/, "");
      const url = `${base}:${port}${path}`;
      const res = await fetch(url, options);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      return data?.answer ?? data?.error ?? null;
    } catch {
      continue;
    }
  }
  return null;
}

/** 回收站项 */
export interface RecycleBinItem {
  recycle_id: string;
  item_id: string;
  skill_id: string;
  name: string;
  source: string;
  deleted_at: string;
}

/** 回收站 API 走 L2（与 move_to_recycle_bin 同进程，路径一致） */
async function fetchRecycleBin<T>(
  path: string,
  options: { method?: string; body?: string } = {}
): Promise<T> {
  const sub = await getSubAccountId();
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (sub) headers["X-Sub-Account-Id"] = sub;
  const res = await fetch(`${BACKEND_URL}${path}`, {
    method: options.method ?? "GET",
    headers,
    body: options.body,
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  return res.json();
}

/** 列出回收站技能 */
export async function listRecycleBinSkills(): Promise<RecycleBinItem[]> {
  try {
    const res = await fetchRecycleBin<{ items?: RecycleBinItem[] }>("/api/v2/recycle-bin/skills");
    return Array.isArray(res?.items) ? res.items : [];
  } catch (e) {
    console.warn("[RecycleBin] list failed:", e);
    return [];
  }
}

/** 从回收站恢复技能 */
export async function restoreRecycleBinSkill(recycleId: string): Promise<{ ok: boolean; error?: string }> {
  try {
    const res = await fetchRecycleBin<{ ok?: boolean; error?: string }>(
      `/api/v2/recycle-bin/skills/${encodeURIComponent(recycleId)}/restore`,
      { method: "POST" }
    );
    return { ok: res?.ok !== false, error: res?.error };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) };
  }
}

/** 从回收站彻底删除 */
export async function permanentDeleteRecycleBinSkill(recycleId: string): Promise<{ ok: boolean; error?: string }> {
  try {
    const res = await fetchRecycleBin<{ ok?: boolean; error?: string }>(
      `/api/v2/recycle-bin/skills/${encodeURIComponent(recycleId)}`,
      { method: "DELETE" }
    );
    return { ok: res?.ok !== false, error: res?.error };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) };
  }
}

/** 列出已隐藏的技能 item_id */
export async function listHiddenSkills(): Promise<string[]> {
  const sub = await getSubAccountId();
  const res = await fetch(`${BACKEND_URL}/api/v2/inventory/skills/hidden`, {
    headers: sub ? { "X-Sub-Account-Id": sub } : {},
  });
  const data = (await res.json().catch(() => ({}))) as { item_ids?: string[] };
  return Array.isArray(data?.item_ids) ? data.item_ids : [];
}

/** 列出已隐藏的 L3 MCP item_id */
export async function listHiddenMcps(): Promise<string[]> {
  const sub = await getSubAccountId();
  const res = await fetch(`${BACKEND_URL}/api/v2/inventory/l3_mcps/hidden`, {
    headers: sub ? { "X-Sub-Account-Id": sub } : {},
  });
  const data = (await res.json().catch(() => ({}))) as { item_ids?: string[] };
  return Array.isArray(data?.item_ids) ? data.item_ids : [];
}

/** 隐藏技能：L2 列表中排除，L3 不可见 */
export async function hideSkill(itemId: string): Promise<{ ok: boolean; error?: string }> {
  const sub = await getSubAccountId();
  const res = await fetch(`${BACKEND_URL}/api/v2/inventory/skills/${encodeURIComponent(itemId)}/hide`, {
    method: "POST",
    headers: sub ? { "X-Sub-Account-Id": sub } : {},
  });
  const data = (await res.json().catch(() => ({}))) as { ok?: boolean; error?: string };
  return { ok: res.ok && data?.ok !== false, error: data?.error };
}

/** 取消隐藏技能 */
export async function unhideSkill(itemId: string): Promise<{ ok: boolean; error?: string }> {
  const sub = await getSubAccountId();
  const res = await fetch(`${BACKEND_URL}/api/v2/inventory/skills/${encodeURIComponent(itemId)}/unhide`, {
    method: "POST",
    headers: sub ? { "X-Sub-Account-Id": sub } : {},
  });
  const data = (await res.json().catch(() => ({}))) as { ok?: boolean; error?: string };
  return { ok: res.ok && data?.ok !== false, error: data?.error };
}

/** L3_LOCAL MCP 信息 */
export interface L3McpInfo {
  item_id: string;
  name: string;
  description?: string;
  tools?: string[];
}

/** 列出 L2 的 L3_LOCAL MCP */
export async function listL3Mcps(): Promise<L3McpInfo[]> {
  const sub = await getSubAccountId();
  const res = await fetch(`${BACKEND_URL}/api/v2/inventory/l3_mcps`, {
    headers: sub ? { "X-Sub-Account-Id": sub } : {},
  });
  const data = (await res.json().catch(() => ({}))) as { mcps?: L3McpInfo[] };
  return Array.isArray(data?.mcps) ? data.mcps : [];
}

/** 隐藏 L3_LOCAL MCP */
export async function hideMcp(itemId: string): Promise<{ ok: boolean; error?: string }> {
  const sub = await getSubAccountId();
  const res = await fetch(`${BACKEND_URL}/api/v2/inventory/l3_mcps/${encodeURIComponent(itemId)}/hide`, {
    method: "POST",
    headers: sub ? { "X-Sub-Account-Id": sub } : {},
  });
  const data = (await res.json().catch(() => ({}))) as { ok?: boolean; error?: string };
  return { ok: res.ok && data?.ok !== false, error: data?.error };
}

/** 取消隐藏 L3_LOCAL MCP */
export async function unhideMcp(itemId: string): Promise<{ ok: boolean; error?: string }> {
  const sub = await getSubAccountId();
  const res = await fetch(`${BACKEND_URL}/api/v2/inventory/l3_mcps/${encodeURIComponent(itemId)}/unhide`, {
    method: "POST",
    headers: sub ? { "X-Sub-Account-Id": sub } : {},
  });
  const data = (await res.json().catch(() => ({}))) as { ok?: boolean; error?: string };
  return { ok: res.ok && data?.ok !== false, error: data?.error };
}

/** 删除 L3_LOCAL MCP（从 inventory 移除） */
export async function deleteMcp(itemId: string): Promise<{ ok: boolean; error?: string }> {
  const sub = await getSubAccountId();
  const res = await fetch(`${BACKEND_URL}/api/v2/inventory/l3_mcps/${encodeURIComponent(itemId)}`, {
    method: "DELETE",
    headers: sub ? { "X-Sub-Account-Id": sub } : {},
  });
  const data = (await res.json().catch(() => ({}))) as { ok?: boolean; error?: string };
  return { ok: res.ok && data?.ok !== false, error: data?.error };
}

/**
 * 获取技能列表（GET /api/v3/skills，从 L3 读取）
 */
export async function listSkills(): Promise<SkillInfo[]> {
  try {
    const list = await invokeL3Skills<SkillInfo[]>("/api/v3/skills", undefined, "GET");
    return Array.isArray(list) ? list : [];
  } catch (e) {
    console.warn("[Skills] L3 不可用，回退 L2:", e);
    const list = await invokeBackend<SkillInfo[]>("/api/v3/skills", undefined, "GET");
    return Array.isArray(list) ? list : [];
  }
}

/** GET /api/v3/config/native-fs-policy — 内置与用户扩展的 Native 读写路径策略 */
export interface NativeFsPolicyPayload {
  ok?: boolean;
  policy_file?: string;
  builtin_write_roots?: string[];
  custom_write_roots?: string[];
  builtin_read_blacklist_lines?: string[];
  custom_read_blacklist_roots?: string[];
  error?: string;
}

async function nativeFsPolicyGetViaTauri(): Promise<NativeFsPolicyPayload> {
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<NativeFsPolicyPayload>("native_fs_policy_get");
}

async function nativeFsPolicySetViaTauri(body: {
  write_allowlist_extra: string[];
  read_blacklist_extra: string[];
}): Promise<{ ok?: boolean; error?: string; message?: string }> {
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke("native_fs_policy_set", { input: body });
}

/**
 * 优先 L3 HTTP（含解析后的内置写入根）；失败时回退 Tauri 直连 `~/.jachin/config/native_fs_policy.json`（与 Python 共用）。
 */
export async function fetchNativeFsPolicy(): Promise<NativeFsPolicyPayload> {
  try {
    const p = await invokeL3Skills<NativeFsPolicyPayload>(
      "/api/v3/config/native-fs-policy",
      undefined,
      "GET"
    );
    if (p && p.ok !== false) return p;
  } catch {
    /* 无 L3 / 404 / 网络错误 → 桌面端读本地文件 */
  }
  return nativeFsPolicyGetViaTauri();
}

export async function saveNativeFsPolicy(body: {
  write_allowlist_extra: string[];
  read_blacklist_extra: string[];
}): Promise<{ ok?: boolean; error?: string; message?: string }> {
  try {
    return await invokeL3Skills<{ ok?: boolean; error?: string; message?: string }>(
      "/api/v3/config/native-fs-policy",
      body,
      "POST"
    );
  } catch {
    return nativeFsPolicySetViaTauri(body);
  }
}

/** HR 透析镜技能 ID（支持流式进度） */
export const HR_SKILL_IDS = ["jpp:com.jachin.hr.analyzer4"];

/** 流式进度事件 */
export interface SkillStreamEvent {
  status: "progress" | "done" | "error";
  filename?: string;
  current?: number;
  total?: number;
  error?: string;
}

const _STEM_TO_NAME: Record<string, string> = {
  zhangsan: "张三",
  lisi: "李四",
  wangwu: "王五",
  zhaoliu: "赵六",
};

/** 解析 filename 为显示名（如 zhangsan_resume.md -> 张三.md） */
export function displayNameFromFilename(filename: string): string {
  const stem = filename.replace(/_resume\.(md|txt)$/i, "").replace(/\.(md|txt)$/i, "");
  if (/^resume_\d+$/.test(stem)) return filename;
  const display = _STEM_TO_NAME[stem.toLowerCase()] ?? stem;
  return display ? `${display}.md` : filename;
}

/** L3 系统日志 SSE 流 URL（供 EventSource 订阅） */
export function getL3LogsStreamUrl(): string {
  const envUrl = import.meta.env.VITE_L3_SKILLS_URL;
  if (envUrl && envUrl.includes("://") && /\d{4,5}/.test(envUrl)) {
    return `${envUrl.replace(/\/$/, "")}/api/system/logs/stream`;
  }
  if (L3_DEV_PROXY) {
    return `${L3_DEV_PROXY}/api/system/logs/stream`;
  }
  return `${L3_SKILLS_BASE.replace(/:\d+$/, "")}:${L3_SKILLS_PORTS[0]}/api/system/logs/stream`;
}

/** 多端口回退：L3 HTTP 可能在 18991-18999 启动，依次尝试 */
export function getL3LogsStreamUrls(): string[] {
  const envUrl = import.meta.env.VITE_L3_SKILLS_URL;
  if (envUrl && envUrl.includes("://") && /\d{4,5}/.test(envUrl)) {
    return [`${envUrl.replace(/\/$/, "")}/api/system/logs/stream`];
  }
  const base = L3_SKILLS_BASE.replace(/:\d+$/, "");
  const urls: string[] = [];
  if (L3_DEV_PROXY) {
    urls.push(`${L3_DEV_PROXY}/api/system/logs/stream`);
  }
  for (const port of L3_SKILLS_PORTS) {
    urls.push(`${base}:${port}/api/system/logs/stream`);
  }
  return [...new Set(urls)];
}

/** Kalaroko E2E 巡检 SSE（同步版仅猜首端口；生产/海外请用 ``resolveKalarokoMonitorStreamUrl``） */
export function getKalarokoMonitorStreamUrl(opts?: {
  runs?: number;
  interval?: number;
  skipPlaywright?: boolean;
}): string {
  const sp = new URLSearchParams();
  if (opts?.runs != null) sp.set("runs", String(opts.runs));
  if (opts?.interval != null) sp.set("interval", String(opts.interval));
  if (opts?.skipPlaywright) sp.set("skip_playwright", "1");
  const q = sp.toString();
  const path = `/api/v1/monitor/stream${q ? `?${q}` : ""}`;
  const envUrl = import.meta.env.VITE_L3_SKILLS_URL;
  if (envUrl && envUrl.includes("://") && /\d{4,5}/.test(envUrl)) {
    return `${envUrl.replace(/\/$/, "")}${path}`;
  }
  if (L3_DEV_PROXY) {
    return `${L3_DEV_PROXY}${path}`;
  }
  return `${L3_SKILLS_BASE.replace(/:\d+$/, "")}:${L3_SKILLS_PORTS[0]}${path}`;
}

/** 巡检 SSE 完整 URL：先 ``getL3SkillsBaseUrl`` 短超时探测，再拼 path（推荐巡检页使用） */
export async function resolveKalarokoMonitorStreamUrl(opts?: {
  runs?: number;
  interval?: number;
  skipPlaywright?: boolean;
}): Promise<string> {
  const base = await getL3SkillsBaseUrl();
  const sp = new URLSearchParams();
  if (opts?.runs != null) sp.set("runs", String(opts.runs));
  if (opts?.interval != null) sp.set("interval", String(opts.interval));
  if (opts?.skipPlaywright) sp.set("skip_playwright", "1");
  const q = sp.toString();
  return `${base}/api/v1/monitor/stream${q ? `?${q}` : ""}`;
}

/** L3 巡检控制台 REST（停止 / 定时调度），base 逻辑与 ``getKalarokoMonitorStreamUrl`` 一致 */
export function getL3MonitorApiUrl(apiPath: string): string {
  const path = apiPath.startsWith("/") ? apiPath : `/${apiPath}`;
  const envUrl = import.meta.env.VITE_L3_SKILLS_URL;
  if (envUrl && envUrl.includes("://") && /\d{4,5}/.test(envUrl)) {
    return `${envUrl.replace(/\/$/, "")}${path}`;
  }
  if (L3_DEV_PROXY) {
    return `${L3_DEV_PROXY}${path}`;
  }
  return `${L3_SKILLS_BASE.replace(/:\d+$/, "")}:${L3_SKILLS_PORTS[0]}${path}`;
}

/** 异步解析 REST 完整 URL（与 ``resolveKalarokoMonitorStreamUrl`` 同源探测，推荐巡检页使用） */
export async function resolveL3MonitorApiUrl(apiPath: string): Promise<string> {
  const base = await getL3SkillsBaseUrl();
  const p = apiPath.startsWith("/") ? apiPath : `/${apiPath}`;
  return `${base}${p}`;
}

/** L3 控制台 K11 冒烟默认测试站（与 SSE `target_url` 查询参数一致，可被 opts.targetUrl 覆盖） */
export const K11_SMOKE_DEFAULT_TARGET_URL = "https://www.kalaroko.com/";

/** K11 统合平台冒烟（Playwright）SSE，base 与 Kalaroko 巡检一致 */
export function getK11UnifiedSmokeStreamUrl(opts?: {
  targetUrl?: string;
  cdpHttp?: string;
  verbose?: boolean;
  noLarkReport?: boolean;
}): string {
  const sp = new URLSearchParams();
  sp.set("target_url", (opts?.targetUrl || K11_SMOKE_DEFAULT_TARGET_URL).trim() || K11_SMOKE_DEFAULT_TARGET_URL);
  if (opts?.cdpHttp) sp.set("cdp_http", opts.cdpHttp);
  if (opts?.verbose) sp.set("verbose", "1");
  if (opts?.noLarkReport) sp.set("no_lark_report", "1");
  const q = sp.toString();
  const path = `/api/v1/k11-unified-smoke/stream${q ? `?${q}` : ""}`;
  const envUrl = import.meta.env.VITE_L3_SKILLS_URL;
  if (envUrl && envUrl.includes("://") && /\d{4,5}/.test(envUrl)) {
    return `${envUrl.replace(/\/$/, "")}${path}`;
  }
  if (L3_DEV_PROXY) {
    return `${L3_DEV_PROXY}${path}`;
  }
  return `${L3_SKILLS_BASE.replace(/:\d+$/, "")}:${L3_SKILLS_PORTS[0]}${path}`;
}

/** P2 仅「浏览器兼容」段（`--only-compat`） */
export function getK11P2CompatOnlyStreamUrl(opts?: {
  targetUrl?: string;
  cdpHttp?: string;
  verbose?: boolean;
  noLarkReport?: boolean;
  headless?: boolean;
}): string {
  const sp = new URLSearchParams();
  sp.set("target_url", (opts?.targetUrl || K11_SMOKE_DEFAULT_TARGET_URL).trim() || K11_SMOKE_DEFAULT_TARGET_URL);
  if (opts?.cdpHttp) sp.set("cdp_http", opts.cdpHttp);
  if (opts?.verbose) sp.set("verbose", "1");
  if (opts?.noLarkReport) sp.set("no_lark_report", "1");
  if (opts?.headless) sp.set("headless", "1");
  const q = sp.toString();
  const path = `/api/v1/k11-p2-compat-only/stream${q ? `?${q}` : ""}`;
  const envUrl = import.meta.env.VITE_L3_SKILLS_URL;
  if (envUrl && envUrl.includes("://") && /\d{4,5}/.test(envUrl)) {
    return `${envUrl.replace(/\/$/, "")}${path}`;
  }
  if (L3_DEV_PROXY) {
    return `${L3_DEV_PROXY}${path}`;
  }
  return `${L3_SKILLS_BASE.replace(/:\d+$/, "")}:${L3_SKILLS_PORTS[0]}${path}`;
}

/**
 * 获取 L3 HTTP base（与 invokeL3Skills / 巡检 SSE 同源）。
 * 未配 ``VITE_L3_SKILLS_URL`` 时：并行短探测 + 成功后短期缓存；不依赖该变量即可使用。
 *
 * @param opts.bypassCache 为 true 时跳过缓存（例如 L3 刚重启换端口）
 */
export async function getL3SkillsBaseUrl(opts?: { bypassCache?: boolean }): Promise<string> {
  const path = "/api/v3/skills";
  const now = Date.now();
  if (!opts?.bypassCache && _l3BaseUrlCache && now < _l3BaseUrlCache.until) {
    return _l3BaseUrlCache.url;
  }

  const envUrl = import.meta.env.VITE_L3_SKILLS_URL;
  if (envUrl && envUrl.includes("://") && /\d{4,5}/.test(envUrl)) {
    const u = envUrl.replace(/\/$/, "");
    _l3BaseUrlCache = { url: u, until: now + L3_BASE_CACHE_MS };
    return u;
  }
  if (L3_DEV_PROXY) {
    const url = `${L3_DEV_PROXY}${path}`;
    if (await fetchL3ProbeOk(url)) {
      _l3BaseUrlCache = { url: L3_DEV_PROXY, until: now + L3_BASE_CACHE_MS };
      return L3_DEV_PROXY;
    }
  }
  const host = L3_SKILLS_BASE.replace(/:\d+$/, "");
  const bases = await Promise.all(
    L3_SKILLS_PORTS.map(async (port) => {
      const base = `${host}:${port}`;
      const ok = await fetchL3ProbeOk(`${base}${path}`);
      return ok ? base : null;
    }),
  );
  const found = bases.find((b) => b != null);
  if (found) {
    _l3BaseUrlCache = { url: found, until: Date.now() + L3_BASE_CACHE_MS };
    return found;
  }
  _l3BaseUrlCache = null;
  throw new Error("L3 技能 API 不可达，请确认 L3 已启动（端口 18991 等）");
}

/** 清除 L3 base 探测缓存（L3 重启/换端口后可调用） */
export function clearL3SkillsBaseUrlCache(): void {
  _l3BaseUrlCache = null;
}

/** 与 ``getL3SkillsBaseUrl`` 等价；K11 / 旧调用兼容别名 */
export async function getL3HttpBaseUrl(): Promise<string> {
  return getL3SkillsBaseUrl();
}

/** 巡检/冒烟 REST：先解析 L3 base 再拼 path（等同 ``resolveL3MonitorApiUrl``，供 K11 控制台） */
export async function getL3MonitorApiUrlAsync(apiPath: string): Promise<string> {
  return resolveL3MonitorApiUrl(apiPath);
}

/** K11 统合冒烟 SSE：先探测 L3 端口再拼 URL */
export async function getK11UnifiedSmokeStreamUrlAsync(opts?: {
  targetUrl?: string;
  cdpHttp?: string;
  verbose?: boolean;
  noLarkReport?: boolean;
  runs?: number;
  interval?: number;
}): Promise<string> {
  const sp = new URLSearchParams();
  sp.set("target_url", (opts?.targetUrl || K11_SMOKE_DEFAULT_TARGET_URL).trim() || K11_SMOKE_DEFAULT_TARGET_URL);
  if (opts?.cdpHttp) sp.set("cdp_http", opts.cdpHttp);
  if (opts?.verbose) sp.set("verbose", "1");
  if (opts?.noLarkReport) sp.set("no_lark_report", "1");
  if (opts?.runs != null) sp.set("runs", String(opts.runs));
  if (opts?.interval != null) sp.set("interval", String(opts.interval));
  const q = sp.toString();
  const rel = `/api/v1/k11-unified-smoke/stream${q ? `?${q}` : ""}`;
  const base = await getL3SkillsBaseUrl();
  return `${base}${rel}`;
}

/** P2 兼容段 SSE：先探测 L3 */
export async function getK11P2CompatOnlyStreamUrlAsync(opts?: {
  targetUrl?: string;
  cdpHttp?: string;
  verbose?: boolean;
  noLarkReport?: boolean;
  headless?: boolean;
  runs?: number;
  interval?: number;
}): Promise<string> {
  const sp = new URLSearchParams();
  sp.set("target_url", (opts?.targetUrl || K11_SMOKE_DEFAULT_TARGET_URL).trim() || K11_SMOKE_DEFAULT_TARGET_URL);
  if (opts?.cdpHttp) sp.set("cdp_http", opts.cdpHttp);
  if (opts?.verbose) sp.set("verbose", "1");
  if (opts?.noLarkReport) sp.set("no_lark_report", "1");
  if (opts?.headless) sp.set("headless", "1");
  if (opts?.runs != null) sp.set("runs", String(opts.runs));
  if (opts?.interval != null) sp.set("interval", String(opts.interval));
  const q = sp.toString();
  const rel = `/api/v1/k11-p2-compat-only/stream${q ? `?${q}` : ""}`;
  const base = await getL3SkillsBaseUrl();
  return `${base}${rel}`;
}

/** K11 游戏状态机轻量冒烟 SSE（`scripts/test_k11_smoke_games_state_machine_playwright.py`） */
export async function getK11GamesStateMachineSmokeStreamUrlAsync(opts?: {
  targetUrl?: string;
  cdpHttp?: string;
  verbose?: boolean;
  noLarkReport?: boolean;
  runs?: number;
  interval?: number;
}): Promise<string> {
  const sp = new URLSearchParams();
  sp.set("target_url", (opts?.targetUrl || K11_SMOKE_DEFAULT_TARGET_URL).trim() || K11_SMOKE_DEFAULT_TARGET_URL);
  if (opts?.cdpHttp) sp.set("cdp_http", opts.cdpHttp);
  if (opts?.verbose) sp.set("verbose", "1");
  if (opts?.noLarkReport) sp.set("no_lark_report", "1");
  if (opts?.runs != null) sp.set("runs", String(opts.runs));
  if (opts?.interval != null) sp.set("interval", String(opts.interval));
  const q = sp.toString();
  const rel = `/api/v1/k11-games-state-machine-smoke/stream${q ? `?${q}` : ""}`;
  const base = await getL3SkillsBaseUrl();
  return `${base}${rel}`;
}

/** K11 游戏模块开门冒烟 SSE：执行 test_k11_game_open_smoke.py */
export async function getK11GameOpenSmokeStreamUrlAsync(opts?: {
  targetUrl?: string;
  verbose?: boolean;
  noLarkReport?: boolean;
  singleGame?: string;
}): Promise<string> {
  const sp = new URLSearchParams();
  sp.set("target_url", (opts?.targetUrl || K11_SMOKE_DEFAULT_TARGET_URL).trim() || K11_SMOKE_DEFAULT_TARGET_URL);
  if (opts?.verbose) sp.set("verbose", "1");
  if (opts?.noLarkReport) sp.set("no_lark_report", "1");
  if (opts?.singleGame) sp.set("single_game", opts.singleGame);
  const q = sp.toString();
  const rel = `/api/v1/k11-game-open-smoke/stream${q ? `?${q}` : ""}`;
  const base = await getL3SkillsBaseUrl();
  return `${base}${rel}`;
}

/** Kalaroko 巡检矩阵 SSE：先探测 L3（等同 ``resolveKalarokoMonitorStreamUrl``） */
export async function getKalarokoMonitorStreamUrlAsync(opts?: {
  runs?: number;
  interval?: number;
  skipPlaywright?: boolean;
}): Promise<string> {
  return resolveKalarokoMonitorStreamUrl(opts);
}

/** 定时 K11 批跑日志（与手动的 /stream 并行） */
export async function getK11ScheduledSmokeLogStreamUrlAsync(): Promise<string> {
  const base = await getL3SkillsBaseUrl();
  return `${base}/api/v1/k11-unified-smoke/schedule/log-stream`;
}

/**
 * 流式执行 HR 透析镜（POST /api/v3/skills/{skill_id}/execute/stream）
 * 返回 SSE 事件异步迭代器，供 BatchProgressBar 消费
 * 若流式不可达则抛出，调用方可回退到 executeSkill
 */
export async function* executeSkillStream(
  skillId: string,
  capabilityName: string,
  inputData: Record<string, unknown> = {}
): AsyncGenerator<SkillStreamEvent> {
  const baseUrl = await getL3SkillsBaseUrl();
  const path = `/api/v3/skills/${encodeURIComponent(skillId)}/execute/stream`;
  const url = `${baseUrl}${path}`;
  const body = JSON.stringify({
    capability_name: capabilityName,
    input_data: inputData,
  });
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`流式接口 HTTP ${res.status}: ${text.slice(0, 200)}`);
  }
  if (!res.body) throw new Error("L3 流式 API 返回无 body，请检查服务端");
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n\n");
      buffer = lines.pop() ?? "";
      for (const block of lines) {
        const m = block.match(/^data:\s*(.+)/);
        if (!m) continue;
        try {
          const ev = JSON.parse(m[1]) as SkillStreamEvent;
          yield ev;
          if (ev.status === "done" || ev.status === "error") return;
        } catch {
          /* ignore parse */
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

/** 判断是否为 HR 透析镜技能（支持流式） */
export function isHrSkill(skillId: string): boolean {
  return HR_SKILL_IDS.includes((skillId || "").trim());
}

/**
 * 执行技能能力（POST /api/v3/skills/{skill_id}/execute，在 L3 执行）
 */
export async function executeSkill(
  skillId: string,
  capabilityName: string,
  inputData: Record<string, unknown> = {}
): Promise<{ success: boolean; result?: unknown; error?: string; wasm_details?: string }> {
  try {
    return await invokeL3Skills(`/api/v3/skills/${encodeURIComponent(skillId)}/execute`, {
      capability_name: capabilityName,
      input_data: inputData,
    }, "POST");
  } catch (e) {
    console.warn("[Skills] L3 执行不可用，回退 L2:", e);
    return invokeBackend(`/api/v3/skills/${encodeURIComponent(skillId)}/execute`, {
      capability_name: capabilityName,
      input_data: inputData,
    });
  }
}

/**
 * 语音识别
 */
export async function recognizeAudio(
  audioFile: File,
  format: string = "wav",
  language: string = "zh-CN"
): Promise<{ text: string; language: string }> {
  const formData = new FormData();
  formData.append("audio_file", audioFile);
  formData.append("format", format);
  formData.append("language", language);

  const response = await fetch(
    `${BACKEND_URL}/api/v2/voice/recognize`,
    {
      method: "POST",
      body: formData,
    }
  );

  if (!response.ok) {
    const error = await response.text();
    throw new Error(error);
  }

  return response.json();
}

/** 语义路由响应（安全指令协议） */
export interface IntentRouteResponse {
  intent_type: "CHAT" | "COMMAND";
  risk_level: "low" | "medium" | "high";
  stripped_text: string;
}

/**
 * 语义路由：将用户文本分类为 CHAT / COMMAND，供前端切换 Alert Mode 与二次确认
 */
export async function routeIntent(text: string): Promise<IntentRouteResponse> {
  const response = await fetch(`${BACKEND_URL}/api/v2/voice/intent`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  if (!response.ok) {
    return { intent_type: "CHAT", risk_level: "low", stripped_text: text };
  }
  return response.json();
}

/** L2 返回 503（TTS 关闭等）后本会话内不再轰炸 /api/v2/voice/synthesize */
let l2TtsHttpSkipped = false;

/**
 * 语音合成
 * 优先使用本地 Kokoro TTS（tts_speak），失败时回退到 Tier 2 Edge TTS
 */
export async function synthesizeSpeech(
  text: string,
  voice: string = "zh-CN-XiaoxiaoNeural",
  language: string = "zh-CN",
  speed: number = 1.0,
  pitch: number = 1.0
): Promise<Blob> {
  // 在 Tauri 中优先尝试本地 TTS
  try {
    const { invoke } = await import("@tauri-apps/api/core");
    const base64 = await invoke<string>("tts_speak", { text });
    const binary = Uint8Array.from(atob(base64), (c) => c.charCodeAt(0));
    return new Blob([binary], { type: "audio/wav" });
  } catch {
    // 本地 TTS 不可用时回退到 Tier 2
  }

  if (l2TtsHttpSkipped) {
    throw new Error("L2 TTS skipped after 503");
  }

  const response = await fetch(`${BACKEND_URL}/api/v2/voice/synthesize`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      text,
      voice,
      language,
      speed,
      pitch,
    }),
  });

  if (!response.ok) {
    if (response.status === 503) {
      l2TtsHttpSkipped = true;
    }
    const error = await response.text();
    throw new Error(error);
  }

  return response.blob();
}

/**
 * 语音聊天（完整流程）
 */
export async function voiceChat(
  audioFile: File,
  format: string = "wav",
  language: string = "zh-CN",
  returnAudio: boolean = true,
  voice: string = "zh-CN-XiaoxiaoNeural"
): Promise<{
  user_text: string;
  text: string;
  audio_base64?: string;
  audio_format?: string;
}> {
  const formData = new FormData();
  formData.append("audio_file", audioFile);
  formData.append("format", format);
  formData.append("language", language);
  formData.append("return_audio", returnAudio.toString());
  formData.append("voice", voice);
  formData.append("speed", "1.0");
  formData.append("pitch", "1.0");

  const response = await fetch(`${BACKEND_URL}/api/v2/voice/chat`, {
    method: "POST",
    body: formData,
  });

  if (!response.ok) {
    const error = await response.text();
    throw new Error(error);
  }

  return response.json();
}

/** 语音处理请求（CTO 契约：双轨 input_mode） */
export interface VoiceProcessRequest {
  wav_base64: string;
  input_mode: "manual" | "vad";
  client_timestamp?: number;
}

/** 语音处理响应（CTO 契约：intent_routing + security_action） */
export interface VoiceProcessResponse {
  status: string;
  recognized_text?: string;
  intent_routing: "ENGAGE" | "IGNORE";
  security_action: "NONE" | "REQUIRE_CONFIRMATION" | "REJECTED";
  reply_text?: string;
  reply_audio_base64?: string;
}

/**
 * 语音处理总线 POST /api/v1/voice/process（Step 2 实现后端）
 * 前端严格传 wav_base64 + input_mode + client_timestamp
 */
export async function voiceProcess(
  wav_base64: string,
  input_mode: "manual" | "vad",
  client_timestamp?: number
): Promise<VoiceProcessResponse> {
  const body: VoiceProcessRequest = {
    wav_base64,
    input_mode,
    client_timestamp: client_timestamp ?? Math.floor(Date.now() / 1000),
  };
  const response = await fetch(`${BACKEND_URL}/api/v1/voice/process`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const err = await response.text();
    throw new Error(err || `voice/process ${response.status}`);
  }
  return response.json();
}

/**
 * 获取可用语音列表
 */
export async function listVoices(
  language?: string
): Promise<{ voices: any[] }> {
  const url = language
    ? `${BACKEND_URL}/api/v2/voice/voices?language=${language}`
    : `${BACKEND_URL}/api/v2/voice/voices`;

  const response = await fetch(url);

  if (!response.ok) {
    const error = await response.text();
    throw new Error(error);
  }

  return response.json();
}

/**
 * 调用插件（通过 Jachin Link 或直接 API）
 */
export interface PluginRequest {
  plugin_id?: string;  // 如果为 undefined，则使用 user_query
  method_name?: string;  // 如果为 undefined，则使用 user_query
  payload?: any;
  trace_id?: string;
  user_query?: string;  // 自然语言查询（用于自动规划）
}

export interface PluginResponse {
  status_code: number;
  error_message?: string;
  payload?: Uint8Array;
  ui_render_schema?: string;
  data_payload?: Uint8Array;
  trace_id?: string;
  metadata?: Record<string, unknown>;
}

/**
 * 卸载技能：优先 Tauri invoke，不可用时走 L3 HTTP DELETE（供浏览器控制台使用）。
 */
export async function uninstallSkill(
  itemId: string,
  purgeData: boolean
): Promise<{ ok: boolean; error?: string }> {
  // 1. 尝试 Tauri invoke（桌面应用内）
  try {
    const { invoke } = await import("@tauri-apps/api/core");
    const res = await invoke<{ ok?: boolean; error?: string }>("uninstall_skill", {
      baseUrl: BACKEND_URL,
      itemId,
      purgeData,
    });
    return { ok: res?.ok !== false, error: res?.error };
  } catch (e) {
    const tauriErr = e instanceof Error ? e.message : String(e);
    // 2. Tauri 不可用（如浏览器打开控制台）时，走 L3 HTTP DELETE
    try {
      const path = `/api/v3/skills/${encodeURIComponent(itemId)}?purge_data=${purgeData}`;
      const res = await invokeL3Skills<{ ok?: boolean; error?: string }>(path, undefined, "DELETE");
      return { ok: res?.ok !== false, error: res?.error };
    } catch (httpErr) {
      return { ok: false, error: tauriErr || (httpErr instanceof Error ? httpErr.message : String(httpErr)) };
    }
  }
}

/**
 * 获取技能配置（GET /api/v2/skills/{skill_id}/config）
 */
export async function getSkillConfig(skillId: string): Promise<Record<string, unknown>> {
  const sub = await getSubAccountId();
  const res = await fetch(`${BACKEND_URL}/api/v2/skills/${encodeURIComponent(skillId)}/config`, {
    headers: sub ? { "X-Sub-Account-Id": sub } : {},
  });
  if (!res.ok) return {};
  const data = await res.json();
  return data?.config ?? {};
}

/**
 * 更新技能配置（PUT /api/v2/skills/{skill_id}/config）
 */
export async function updateSkillConfig(
  skillId: string,
  configData: Record<string, unknown>
): Promise<{ ok: boolean; updated?: number; inserted?: number; error?: string }> {
  const sub = await getSubAccountId();
  const res = await fetch(`${BACKEND_URL}/api/v2/skills/${encodeURIComponent(skillId)}/config`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
      ...(sub ? { "X-Sub-Account-Id": sub } : {}),
    },
    body: JSON.stringify(configData),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) return { ok: false, error: data?.detail ?? data?.error ?? "更新失败" };
  return { ok: true, updated: data?.updated ?? 0, inserted: data?.inserted ?? 0 };
}

async function getSubAccountId(): Promise<string | null> {
  try {
    const { invoke } = await import("@tauri-apps/api/core");
    const cfg = await invoke<{ sub_account_id?: string }>("read_l2_gateway_config");
    const id = cfg?.sub_account_id;
    return typeof id === "string" && id ? id : null;
  } catch {
    return null;
  }
}

/**
 * 调用插件（简化版，通过 HTTP API）
 * 自然语言查询时，若 L2 编排器返回 404（无匹配插件），自动回退到 L3 Agent，
 * 使 HR 透析镜等 Wasm 技能也能通过控制台自然语言执行并生成输出文档。
 * TODO: 实现 gRPC 客户端连接 Jachin Link Gateway
 */
export async function invokePlugin(
  pluginIdOrQuery: string,
  methodName?: string,
  payload?: any
): Promise<PluginResponse> {
  // 如果 methodName 未提供，则视为自然语言查询
  const isNaturalLanguage = !methodName;

  // 自然语言：先尝试 L2 编排器
  if (isNaturalLanguage) {
    const response = await fetch(`${BACKEND_URL}/api/v3/orchestrator/invoke`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        user_query: pluginIdOrQuery,
        payload: payload || undefined,
        trace_id: `trace-${Date.now()}`,
      }),
    });

    if (response.ok) {
      const result = await response.json();
      return {
        status_code: result.status_code || 200,
        error_message: result.error_message,
        ui_render_schema: result.ui_render_schema,
        data_payload: result.data_payload ? (() => {
          const binary = atob(result.data_payload);
          const bytes = new Uint8Array(binary.length);
          for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
          return bytes;
        })() : undefined,
        trace_id: result.trace_id,
        metadata: result.metadata,
      };
    }

    // 404：L2 无匹配插件，回退到 L3 Agent（会触发 run_tool 持久化，如 HR 透析镜）
    if (response.status === 404) {
      try {
        const agentRes = await invokeL3Skills<{ answer?: string; saved_path?: string; error?: string }>(
          "/api/v3/agent/run",
          { user_input: pluginIdOrQuery },
          "POST"
        );
        const answer = agentRes.answer ?? agentRes.error ?? "";
        const savedPath = agentRes.saved_path;
        return {
          status_code: 200,
          error_message: agentRes.error && !agentRes.answer ? agentRes.error : undefined,
          trace_id: `trace-${Date.now()}`,
          metadata: {
            result: answer,
            saved_path: savedPath,
            chain: [
              { id: "1", label: "用户输入", type: "input" },
              { id: "2", label: "L3 Agent 执行（编排器无匹配，已回退）", type: "skill" },
              { id: "3", label: savedPath ? `完成 · 报告已保存至 ${savedPath}` : "完成", type: "done" },
            ],
          },
        };
      } catch (l3Err) {
        const msg = l3Err instanceof Error ? l3Err.message : String(l3Err);
        if (msg.includes("503") || msg.includes("Agent 尚未就绪")) {
          throw new Error("L3 Agent 尚未就绪，请确保 L3 已启动。若使用 --ws-only 模式，请先启动 L3 节点。");
        }
        throw new Error(`编排器无匹配插件，L3 回退失败: ${msg}`);
      }
    }

    const error = await response.text();
    throw new Error(`Plugin invocation failed: ${error}`);
  }

  // 指定 plugin_id + method_name：直接走 L2
  const response = await fetch(`${BACKEND_URL}/api/v3/orchestrator/invoke`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      plugin_id: pluginIdOrQuery,
      method_name: methodName,
      payload: payload || undefined,
      trace_id: `trace-${Date.now()}`,
    }),
  });

  if (!response.ok) {
    const error = await response.text();
    throw new Error(`Plugin invocation failed: ${error}`);
  }

  const result = await response.json();
  return {
    status_code: result.status_code || 200,
    error_message: result.error_message,
    ui_render_schema: result.ui_render_schema,
    data_payload: result.data_payload ? (() => {
      const binary = atob(result.data_payload);
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
      return bytes;
    })() : undefined,
    trace_id: result.trace_id,
    metadata: result.metadata,
  };
}
