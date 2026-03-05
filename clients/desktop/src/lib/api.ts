/**
 * API Client - 与后端通信
 *
 * V2: Dapr 已废弃，统一直连后端 API。
 */

/** 后端 base URL */
export const BACKEND_URL = import.meta.env.VITE_BACKEND_URL || "http://localhost:18888";

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
  onChunk: (text: string) => void
): Promise<string> {
  const url = `${BACKEND_URL}/api/v2/chat/text`;
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json; charset=utf-8" },
    body: JSON.stringify({
      messages: [{ role: "user", content: message }],
      stream: true,
    }),
  });
  if (!res.ok) throw new Error(`Stream chat failed: ${res.status}`);
  const reader = res.body?.getReader();
  if (!reader) throw new Error("No response body");
  const decoder = new TextDecoder("utf-8");
  let fullText = "";
  const buf: string[] = [];
  while (true) {
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
 */
export async function getClusterStats(): Promise<ClusterStats> {
  return invokeBackend<ClusterStats>("/api/v3/cluster/stats", undefined, "GET");
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
 * 获取记忆数量（GET /api/v3/memory/count，供 Void 节点数）
 */
export async function getMemoryCount(): Promise<{ count: number }> {
  return invokeBackend<{ count: number }>("/api/v3/memory/count", undefined, "GET");
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
 * 记忆搜索（GET /api/v3/memory/search）
 */
export async function searchMemory(
  q: string
): Promise<{ results: MemorySearchResult[]; message?: string }> {
  return invokeBackend<{ results: MemorySearchResult[]; message?: string }>(
    `/api/v3/memory/search?q=${encodeURIComponent(q)}`,
    undefined,
    "GET"
  );
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
 * 批量删除记忆（POST /api/v3/memory/batch-delete，框选遗忘）
 */
export async function batchDeleteMemory(
  memoryIds: string[]
): Promise<{ ok: boolean; deleted?: number; message?: string }> {
  return invokeBackend("/api/v3/memory/batch-delete", { ids: memoryIds });
}

/**
 * 删除记忆（DELETE /api/v3/memory/{id}，单条遗忘）
 */
export async function deleteMemory(memoryId: string): Promise<{ ok: boolean; message?: string }> {
  return invokeBackend<{ ok: boolean; message?: string }>(
    `/api/v3/memory/${encodeURIComponent(memoryId)}`,
    undefined,
    "DELETE"
  );
}

/**
 * 获取设备列表（GET /api/v2/devices）
 */
export async function getDevices(): Promise<DeviceStatus[]> {
  const res = await invokeBackend<{ devices: Array<{
    device_id: string;
    device_type?: string;
    location?: string;
    capabilities: Array<{ name: string }>;
    metadata?: Record<string, unknown>;
    timestamp?: number;
    online: boolean;
  }> }>("/api/v2/devices", undefined, "GET");
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
 * 获取技能列表（GET /api/v3/skills）
 */
export async function listSkills(): Promise<SkillInfo[]> {
  const list = await invokeBackend<SkillInfo[]>("/api/v3/skills", undefined, "GET");
  return Array.isArray(list) ? list : [];
}

/**
 * 执行技能能力（POST /api/v3/skills/{skill_id}/execute）
 */
export async function executeSkill(
  skillId: string,
  capabilityName: string,
  inputData: Record<string, unknown> = {}
): Promise<{ success: boolean; result?: unknown; error?: string }> {
  return invokeBackend(`/api/v3/skills/${encodeURIComponent(skillId)}/execute`, {
    capability_name: capabilityName,
    input_data: inputData,
  });
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
  metadata?: Record<string, string>;
}

/**
 * 调用插件（简化版，通过 HTTP API）
 * TODO: 实现 gRPC 客户端连接 Jachin Link Gateway
 */
export async function invokePlugin(
  pluginIdOrQuery: string,
  methodName?: string,
  payload?: any
): Promise<PluginResponse> {
  // 如果 methodName 未提供，则视为自然语言查询
  const isNaturalLanguage = !methodName;
  
  // 目前通过 HTTP API 调用，后续可以改为 gRPC
  const response = await fetch(`${BACKEND_URL}/api/v3/orchestrator/invoke`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      ...(isNaturalLanguage
        ? { user_query: pluginIdOrQuery }
        : { plugin_id: pluginIdOrQuery, method_name: methodName }),
      payload: payload || undefined,
      trace_id: `trace-${Date.now()}`,
    }),
  });

  if (!response.ok) {
    const error = await response.text();
    throw new Error(`Plugin invocation failed: ${error}`);
  }

  const result = await response.json();
  
  // 转换响应格式
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
