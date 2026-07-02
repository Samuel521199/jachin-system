/**
 * useSensoryWebSocket - Layer 3 全息感官总线连接
 * 连接 ws://localhost:18981/sensory，接收大脑 step_type / thought / action / HITL_REQUIRED
 * v8.0 视觉觉醒：stream_chunk 流式神经、handoff 人格切换、swarm 算力雷达
 */

import { useState, useEffect, useCallback, useRef, type MutableRefObject } from "react";
import { mergeStreamChunk } from "../utils/streamChunkMerge";

const SENSORY_WS_PORT = import.meta.env.VITE_SENSORY_WS_PORT || "18981";
/** 与提示文案 ws://localhost:18981 一致；部分 Windows/Tauri WebView 下 localhost 比 127.0.0.1 更稳 */
const SENSORY_WS_HOST = import.meta.env.VITE_SENSORY_WS_HOST || "localhost";
const SENSORY_WS_URL = `ws://${SENSORY_WS_HOST}:${SENSORY_WS_PORT}/sensory`;
const RECONNECT_DELAY_MS = 3000;

/** Lark 镜像模式：从 VITE_LARK_CHAT_ID 或参数传入，终端作为主屏、Lark 为副屏同步显示 */
const DEFAULT_LARK_CHAT_ID = import.meta.env.VITE_LARK_CHAT_ID || "";

/** v8.0 能力协商：声明 stream_chunk 以接收逐 token 推送 */
const MANIFEST_CAPS = ["ui_render", "hitl_popup", "stream_chunk"];

export interface UseSensoryOptions {
  /** Lark chat_id：启用镜像模式，Lark 消息同步到终端，终端回复同步到 Lark */
  larkChatId?: string;
  /**
   * 无 Lark 镜像时：发往 L3 的会话隔离键（与 `chat_id` / `session_id` 一致）。
   * 有 `larkChatId` 时仍以 Lark 为准，忽略此 ref。
   */
  desktopSessionIdRef?: MutableRefObject<string>;
}

/** L5 定时记忆整理：服务端推送的倒计时提示（勿当 thought 拼进助手气泡） */
export interface MemoryCompactSuggestState {
  content: string;
  countdownSec: number;
  remainingSec: number;
  intervalDays: number;
}

/** 与 L3 Sensory 总线对齐；兼容 `action_type` 与顶层 `metadata` */
export interface SensoryPayload {
  step_type: string;
  /** 部分后端使用 action_type，与 step_type 等价 */
  action_type?: string;
  content: string;
  source?: string;
  task_id?: string;
  run_id?: string;
  tool?: string;
  payload?: Record<string, unknown>;
  metadata?: {
    tool_name?: string;
    error?: string;
    [key: string]: unknown;
  };
}

/** Handoff 人格切换事件 */
export interface HandoffEvent {
  persona: string;
  displayName: string;
}

/** Swarm 任务分发/完成事件 */
export interface SwarmEvent {
  taskId: string;
  type: "offer" | "assigned" | "completed";
  tool?: string;
}

/** 断电/崩溃遗留的后台任务摘要（L3 启动时广播；无 task_id） */
export interface ZombieTaskSummary {
  task_id?: string;
  task_prompt?: string;
  previous_status?: string;
}

/** 后台任务单行进度（与 L3 ``pulse_line`` 一致，仅 ``.``；行满回卷时为空串） */
export interface BackgroundTaskPulseState {
  taskId: string;
  line: string;
}

/** L3 `l3_event_bus` 推送的后台任务事件（须先 `subscribe_background_tasks`） */
export interface BackgroundTaskEventPayload {
  type: "background_task";
  event:
    | "queued"
    | "started"
    | "pulse"
    | "completed"
    | "failed"
    | "cancelled"
    | "zombie_tasks_pending";
  /** 生命周期事件必填；`zombie_tasks_pending` 无此项 */
  task_id?: string;
  ts?: number;
  result_preview?: string;
  message?: string;
  intent_preview?: string;
  queue_hint?: string;
  /** `event === "pulse"`：当前行已展示的 ``.`` 串（回卷后可能为 `""`） */
  pulse_line?: string;
  /** 仅 `event === "zombie_tasks_pending"` */
  count?: number;
  tasks?: ZombieTaskSummary[];
}

/** 桌面横幅：断电遗留任务提醒（由 hook 内 state 驱动） */
export interface ZombieTasksPendingBanner {
  count: number;
  tasks: ZombieTaskSummary[];
}

/** 随 answer 回调：用于区分「仅有流式拼气泡」与「无 chunk 时由 step 注入 ### 回复」；runId 对齐 L3 WS 防超时后陈旧 answer 污染新气泡 */
export interface SensoryAnswerMeta {
  hadStreamChunks?: boolean;
  runId?: string;
  /** L3 帧：answer / rejected / error（哨兵通知等） */
  terminalOutcome?: "answer" | "rejected" | "error";
}

/** 当前流式 chunk 语义：思考 reasoning vs 正文 content（驱动 Jachin Core THINKING / STREAMING） */
export type StreamChunkKind = "reasoning" | "content";

/** 传给 Chat 的 chunk 元信息：双通道 + 服务端 reasoning_content 并行追加 */
export type SensoryChunkMeta = {
  isReasoning?: boolean;
  /** metadata.reasoning_content / 顶层 reasoning：与正文 delta 同时写入思考链 */
  reasoningAppend?: string;
};

export interface SensoryUserInputMeta {
  source: "local" | "mirror";
  runId?: string;
}

function detectChunkIsReasoning(
  meta: Record<string, unknown> | undefined,
  raw: Record<string, unknown>,
  chunkContent: string,
): boolean {
  const m = meta ?? {};
  if (m.is_reasoning === true || m.is_reasoning === "true") return true;
  if (m.isReasoning === true || m.isReasoning === "true") return true;
  const contentEmpty = !String(chunkContent ?? "").trim();
  const rc = m.reasoning_content ?? m.reasoningContent;
  if (typeof rc === "string" && rc.length > 0 && contentEmpty) return true;
  if (typeof raw.reasoning === "string" && raw.reasoning.length > 0 && contentEmpty) return true;
  return false;
}

/** Debug 风格：时间戳 + run/task/tool/source，写入思考链 */
function formatSensoryDebugBlock(data: SensoryPayload, phase: string, body: string): string {
  const ts = new Date().toISOString().replace("T", " ").slice(0, 23);
  const bits: string[] = [`phase=${phase}`];
  bits.push(`step=${data.step_type}`);
  if (data.run_id) bits.push(`run=${String(data.run_id).slice(0, 20)}`);
  if (data.task_id) bits.push(`task=${String(data.task_id).slice(0, 20)}`);
  if (data.tool) bits.push(`tool=${data.tool}`);
  if (data.source) bits.push(`src=${data.source}`);
  const mn = data.metadata?.tool_name;
  if (mn) bits.push(`tool_name=${String(mn)}`);
  const err = data.metadata?.error;
  if (err) bits.push(`err=${String(err).slice(0, 160)}`);
  const head = `[${ts}] [jachin] ${bits.join(" | ")}`;
  return `\n${"─".repeat(52)}\n${head}\n\n${body.trim()}\n`;
}

export function useSensoryWebSocket(options: UseSensoryOptions = {}) {
  const larkChatId = (options.larkChatId ?? DEFAULT_LARK_CHAT_ID).trim();
  const desktopSessionIdRef = options.desktopSessionIdRef;
  const [connected, setConnected] = useState(false);
  const [lastPayload, setLastPayload] = useState<SensoryPayload | null>(null);
  const [hitlPending, setHitlPending] = useState<SensoryPayload | null>(null);
  /** v8.0 流式神经：当前 run 的累积内容，收到 answer 时清空 */
  const [streamingContent, setStreamingContent] = useState("");
  /** 最近一条 chunk 是思考流还是正文流（与 metadata / reasoning 字段同步） */
  const [streamChunkKind, setStreamChunkKind] = useState<StreamChunkKind | null>(null);
  const [currentRunId, setCurrentRunId] = useState<string | null>(null);
  /** Handoff 人格切换：供 UI 触发主题色突变 */
  const [handoffEvent, setHandoffEvent] = useState<HandoffEvent | null>(null);
  /** Swarm 算力雷达：task_offer 时显示扫描，task_completed 时爆发 */
  const [swarmEvent, setSwarmEvent] = useState<SwarmEvent | null>(null);
  /** 记忆整理周期到期：横幅 + 倒计时，结束发 memory_compact_auto_start */
  const [memoryCompactSuggest, setMemoryCompactSuggest] = useState<MemoryCompactSuggestState | null>(null);
  /** L3 启动时推送：上次未闭环的后台任务（zombie_tasks.json） */
  const [zombieTasksPending, setZombieTasksPending] = useState<ZombieTasksPendingBanner | null>(null);
  /** 后台任务执行中单行 ``.`` 进度（`started` / `pulse` 更新，`completed` 等清除） */
  const [backgroundTaskPulse, setBackgroundTaskPulse] = useState<BackgroundTaskPulseState | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const onChunkRef = useRef<((chunk: string, runId: string, meta?: SensoryChunkMeta) => void) | null>(
    null,
  );
  const onAnswerRef = useRef<((content: string, meta?: SensoryAnswerMeta) => void) | null>(null);
  const onStepRef = useRef<((stepType: string, content: string, runId?: string) => void) | null>(null);
  const onMirrorInputRef = useRef<((content: string) => void) | null>(null);
  const onBackgroundTaskRef = useRef<((ev: BackgroundTaskEventPayload) => void) | null>(null);
  const onUserInputRef = useRef<((content: string, meta?: SensoryUserInputMeta) => void) | null>(null);
  /** 本轮是否已收到流式 chunk（有则 answer 勿再向同气泡追加全文，否则会「复读机」） */
  const hadStreamChunksForRunRef = useRef(false);
  /** 与 streamingContent 同步，用于合并 cumulative/delta chunk，避免重复拼接 */
  const streamingAccRef = useRef("");
  /** 用户停止后丢弃残余 chunk/step，直到收到本轮终结帧（answer/rejected/error）以复位内部缓冲 */
  const dropL3StreamUntilTerminalRef = useRef(false);

  /** 注册 Lark 镜像输入回调：Lark 用户发消息时，终端同步显示 */
  const registerMirrorInputHandler = useCallback((fn: ((content: string) => void) | null) => {
    onMirrorInputRef.current = fn;
  }, []);

  /** 注册后台任务事件（完成/失败等）：须与 WS `subscribe_background_tasks` 配合 */
  const registerBackgroundTaskHandler = useCallback(
    (fn: ((ev: BackgroundTaskEventPayload) => void) | null) => {
      onBackgroundTaskRef.current = fn;
    },
    [],
  );

  /** 注册用户输入回调：主窗/HUD/Lark mirror 的用户发言都可实时分发给 UI */
  const registerUserInputHandler = useCallback((fn: ((content: string, meta?: SensoryUserInputMeta) => void) | null) => {
    onUserInputRef.current = fn;
  }, []);

  /** 注册 chunk 回调：供 Chat 将流式内容追加到当前 Assistant 消息 */
  const registerChunkHandler = useCallback(
    (fn: ((chunk: string, runId: string, meta?: SensoryChunkMeta) => void) | null) => {
      onChunkRef.current = fn;
    },
    [],
  );

  /** 注册 answer 回调：收到最终回复时调用（content 与 Lark 推送同源；meta.hadStreamChunks 表示本轮曾收到 chunk） */
  const registerAnswerHandler = useCallback((fn: ((content: string, meta?: SensoryAnswerMeta) => void) | null) => {
    onAnswerRef.current = fn;
  }, []);

  /** 注册 step 回调：thought/action/observation 等思考过程，供 Chat 完整展示 */
  const registerStepHandler = useCallback((fn: ((stepType: string, content: string, runId?: string) => void) | null) => {
    onStepRef.current = fn;
  }, []);

  const dismissMemoryCompactSuggest = useCallback(() => {
    setMemoryCompactSuggest(null);
  }, []);

  const dismissZombieTasksPending = useCallback(() => {
    setZombieTasksPending(null);
  }, []);

  const sendMemoryCompactControl = useCallback(
    (type: "memory_compact_confirm" | "memory_compact_defer" | "memory_compact_cancel", hours?: number) => {
      if (wsRef.current?.readyState !== WebSocket.OPEN) return false;
      const p: Record<string, unknown> = { type };
      if (type === "memory_compact_defer") p.hours = hours ?? 24;
      wsRef.current.send(JSON.stringify(p));
      setMemoryCompactSuggest(null);
      return true;
    },
    [],
  );

  /** 通知 L3 取消当前 run_agent 任务；不断开 WS，后续残余帧由 dropL3StreamUntilTerminalRef 吞掉直至终结包 */
  const sendRunAbort = useCallback(() => {
    dropL3StreamUntilTerminalRef.current = true;
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      const payload: Record<string, string> = { type: "run_abort" };
      if (larkChatId) {
        payload.chat_id = larkChatId;
        payload.session_id = larkChatId;
      } else {
        const sid = desktopSessionIdRef?.current?.trim() ?? "";
        if (sid) {
          payload.chat_id = sid;
          payload.session_id = sid;
        }
      }
      try {
        wsRef.current.send(JSON.stringify(payload));
      } catch {
        /* noop */
      }
    }
    streamingAccRef.current = "";
    hadStreamChunksForRunRef.current = false;
    setStreamChunkKind(null);
    setStreamingContent("");
    setCurrentRunId(null);
  }, [larkChatId, desktopSessionIdRef]);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    try {
      const ws = new WebSocket(SENSORY_WS_URL);
      wsRef.current = ws;

      ws.onopen = () => {
        setConnected(true);
        // v8.0 能力协商：声明 stream_chunk 以接收逐 token 推送
        ws.send(JSON.stringify({ type: "manifest", caps: MANIFEST_CAPS }));
        // 后台任务完成/失败推送（l3_event_bus → broadcast_background_task_event）
        ws.send(JSON.stringify({ type: "subscribe_background_tasks" }));
        // Lark 镜像：订阅后，Lark 消息会以 mirror_input 推送到此终端
        if (larkChatId) {
          ws.send(JSON.stringify({ type: "subscribe_mirror", lark_chat_id: larkChatId }));
        }
      };

      ws.onmessage = (event) => {
        try {
          /**
           * 与 v0.8.98 一致：`JSON.parse` 后直接按 `step_type` + `content` 驱动 chunk/answer。
           * 仅做最小别名：action_type / type → step_type；忽略 manifest_ack 等控制帧（不污染 lastPayload）。
           */
          const raw = JSON.parse(event.data) as Record<string, unknown>;
          // 断电遗留：无 task_id，须先于下方生命周期分支处理
          if (raw.type === "background_task" && raw.event === "zombie_tasks_pending") {
            const count = typeof raw.count === "number" ? raw.count : 0;
            const taskArr = Array.isArray(raw.tasks) ? raw.tasks : [];
            const tasks: ZombieTaskSummary[] = taskArr
              .filter((t): t is Record<string, unknown> => t != null && typeof t === "object")
              .map((t) => ({
                task_id: typeof t.task_id === "string" ? t.task_id : undefined,
                task_prompt: typeof t.task_prompt === "string" ? t.task_prompt : undefined,
                previous_status: typeof t.previous_status === "string" ? t.previous_status : undefined,
              }));
            if (count > 0 || tasks.length > 0) {
              setZombieTasksPending({ count: count || tasks.length, tasks });
            }
            const ev: BackgroundTaskEventPayload = {
              type: "background_task",
              event: "zombie_tasks_pending",
              count: count || tasks.length,
              tasks,
            };
            onBackgroundTaskRef.current?.(ev);
            return;
          }
          if (raw.type === "background_task" && typeof raw.event === "string" && typeof raw.task_id === "string") {
            const tid = raw.task_id as string;
            const evName = raw.event as string;
            if (evName === "started") {
              setBackgroundTaskPulse({ taskId: tid, line: "" });
            } else if (evName === "pulse" && typeof raw.pulse_line === "string") {
              setBackgroundTaskPulse({ taskId: tid, line: raw.pulse_line });
            } else if (evName === "completed" || evName === "failed" || evName === "cancelled") {
              setBackgroundTaskPulse((prev) => (prev?.taskId === tid ? null : prev));
            }
            const ev: BackgroundTaskEventPayload = {
              type: "background_task",
              event: raw.event as BackgroundTaskEventPayload["event"],
              task_id: tid,
              ts: typeof raw.ts === "number" ? raw.ts : undefined,
              result_preview: typeof raw.result_preview === "string" ? raw.result_preview : undefined,
              message: typeof raw.message === "string" ? raw.message : undefined,
              intent_preview: typeof raw.intent_preview === "string" ? raw.intent_preview : undefined,
              queue_hint: typeof raw.queue_hint === "string" ? raw.queue_hint : undefined,
              pulse_line: typeof raw.pulse_line === "string" ? raw.pulse_line : undefined,
            };
            onBackgroundTaskRef.current?.(ev);
            return;
          }
          if (raw.type === "background_task_subscribed") {
            return;
          }
          const step =
            (typeof raw.step_type === "string" && raw.step_type) ||
            (typeof raw.action_type === "string" && raw.action_type) ||
            (typeof raw.type === "string" && raw.type) ||
            "";
          if (step === "memory_compact_suggest") {
            const metaRaw =
              raw.metadata && typeof raw.metadata === "object"
                ? (raw.metadata as Record<string, unknown>)
                : undefined;
            const cd = Math.max(3, Math.min(120, Number(metaRaw?.countdown_sec) || 10));
            const id = Math.max(1, Number(metaRaw?.interval_days) || 3);
            const suggestBody =
              typeof raw.content === "string"
                ? raw.content
                : raw.content != null
                  ? String(raw.content)
                  : "";
            setMemoryCompactSuggest({
              content: suggestBody,
              countdownSec: cd,
              remainingSec: cd,
              intervalDays: id,
            });
            return;
          }
          if (step === "manifest_ack" || step === "manifest" || step === "ping" || step === "pong") {
            return;
          }
          const content =
            typeof raw.content === "string"
              ? raw.content
              : raw.content != null
                ? String(raw.content)
                : "";
          const payloadNested =
            raw.payload && typeof raw.payload === "object"
              ? (raw.payload as Record<string, unknown>)
              : undefined;
          const meta =
            (raw.metadata && typeof raw.metadata === "object"
              ? (raw.metadata as SensoryPayload["metadata"])
              : undefined) ??
            (payloadNested?.metadata && typeof payloadNested.metadata === "object"
              ? (payloadNested.metadata as SensoryPayload["metadata"])
              : undefined);
          const data: SensoryPayload = {
            step_type: step,
            action_type: typeof raw.action_type === "string" ? raw.action_type : undefined,
            content,
            source: typeof raw.source === "string" ? raw.source : undefined,
            task_id: typeof raw.task_id === "string" ? raw.task_id : undefined,
            run_id: typeof raw.run_id === "string" ? raw.run_id : undefined,
            tool: typeof raw.tool === "string" ? raw.tool : undefined,
            payload: payloadNested as Record<string, unknown> | undefined,
            metadata: meta,
          };
          setLastPayload(data);

          // Lark 镜像：Lark 用户发消息时同步到终端显示
          if (data.step_type === "mirror_input" && data.content != null) {
            onMirrorInputRef.current?.(data.content);
            onUserInputRef.current?.(data.content, { source: "mirror", runId: data.run_id });
          }

          if (data.step_type === "HITL_REQUIRED") {
            setHitlPending(data);
          }

          // v8.0 流式神经：合并 chunk（支持全量累加或纯增量），仅把新增 delta 交给 Chat 气泡
          if (data.step_type === "chunk" && data.content != null) {
            if (dropL3StreamUntilTerminalRef.current) {
              return;
            }
            const runId = data.run_id ?? "";
            const metaObj = meta as Record<string, unknown> | undefined;
            const isReasoningChunk = detectChunkIsReasoning(metaObj, raw, data.content);
            setStreamChunkKind(isReasoningChunk ? "reasoning" : "content");
            const { next, delta } = mergeStreamChunk(streamingAccRef.current, data.content);
            streamingAccRef.current = next;
            setStreamingContent(next);
            setCurrentRunId(runId);
            if (delta) {
              hadStreamChunksForRunRef.current = true;
              const chunkMeta: SensoryChunkMeta = { isReasoning: isReasoningChunk };
              // 正文通道可与 metadata.reasoning_content 并行；纯思考通道不再重复追加
              if (!isReasoningChunk) {
                const fromMeta =
                  typeof metaObj?.reasoning_content === "string"
                    ? metaObj.reasoning_content.trim()
                    : typeof metaObj?.reasoningContent === "string"
                      ? metaObj.reasoningContent.trim()
                      : "";
                const rawTop = raw as Record<string, unknown>;
                const fromTop = typeof rawTop.reasoning === "string" ? String(rawTop.reasoning).trim() : "";
                let extra = fromMeta;
                if (fromTop && fromTop !== fromMeta && !fromMeta.includes(fromTop)) {
                  extra = fromMeta ? `${fromMeta}\n\n${fromTop}` : fromTop;
                }
                if (extra) chunkMeta.reasoningAppend = extra;
              }
              onChunkRef.current?.(delta, runId, chunkMeta);
            }
          }

          // thought/action/observation：完整展示思考过程（debug 块 + 原文）
          if (["thought", "action", "observation"].includes(data.step_type) && data.content != null) {
            if (dropL3StreamUntilTerminalRef.current) {
              return;
            }
            const labels: Record<string, string> = { thought: "思考", action: "动作", observation: "观察" };
            const label = labels[data.step_type] ?? data.step_type;
            const inner = `### ${label}\n\n${data.content}`;
            onStepRef.current?.(
              data.step_type,
              formatSensoryDebugBlock(data, data.step_type, inner),
              data.run_id ?? "",
            );
          }

          // 网关 / 环境嗅探 / 拓扑校验等微状态（content 常为 JSON.stringify({ status })）
          if (data.step_type === "system_status" && data.content != null) {
            let line = String(data.content);
            try {
              const j = JSON.parse(line) as { status?: string };
              if (j?.status) line = j.status;
            } catch {
              /* 非 JSON 则原样展示 */
            }
            onStepRef.current?.(
              "system_status",
              formatSensoryDebugBlock(data, "system_status", `### 系统状态\n\n${line}`),
              data.run_id ?? "",
            );
          }

          // answer/rejected/error 结束时：先追加到思考过程，再通知回调
          if (["answer", "rejected", "error"].includes(data.step_type)) {
            const content = data.content ?? "";
            const hadChunks = hadStreamChunksForRunRef.current;
            hadStreamChunksForRunRef.current = false;
            streamingAccRef.current = "";
            const swallowAfterAbort = dropL3StreamUntilTerminalRef.current;
            dropL3StreamUntilTerminalRef.current = false;
            setStreamChunkKind(null);
            setStreamingContent("");
            setCurrentRunId(null);
            if (swallowAfterAbort) {
              return;
            }
            const terminalOutcome = data.step_type as "answer" | "rejected" | "error";
            onAnswerRef.current?.(content, {
              hadStreamChunks: hadChunks,
              runId: data.run_id ?? "",
              terminalOutcome,
            });
          }

          // v8.0 Handoff：解析 [System] 灵魂传输完成... 触发人格色彩突变（持久至下次切换）
          if (data.step_type === "observation" && data.content?.includes("[System] 灵魂传输完成")) {
            const m = data.content.match(/你现在是\s*([^。]+)/);
            const displayName = m?.[1]?.trim() ?? "全能助理";
            const persona =
              displayName.includes("架构师") ? "architect" :
              displayName.includes("分析师") ? "researcher" : "default";
            setHandoffEvent({ persona, displayName });
          }

          // v8.0 Swarm：task_offer 显示雷达扫描 + 思考链 debug 行
          if (data.step_type === "task_offer" && data.task_id) {
            setSwarmEvent({ taskId: data.task_id, type: "offer", tool: data.tool });
            if (!dropL3StreamUntilTerminalRef.current) {
              const pl = data.payload ? JSON.stringify(data.payload).slice(0, 400) : "";
              onStepRef.current?.(
                "task_offer",
                formatSensoryDebugBlock(
                  data,
                  "swarm",
                  `### Swarm · 任务邀请\n- task_id: ${data.task_id}\n- tool: ${data.tool ?? "(none)"}${pl ? `\n- payload: ${pl}` : ""}`,
                ),
                data.run_id ?? "",
              );
            }
          }
          if (data.step_type === "task_assigned" && data.task_id) {
            setSwarmEvent({ taskId: data.task_id, type: "assigned", tool: data.tool });
            if (!dropL3StreamUntilTerminalRef.current) {
              onStepRef.current?.(
                "task_assigned",
                formatSensoryDebugBlock(
                  data,
                  "swarm",
                  `### Swarm · 已分配\n- task_id: ${data.task_id}\n- tool: ${data.tool ?? "(none)"}`,
                ),
                data.run_id ?? "",
              );
            }
          }
          if (data.step_type === "task_completed" && data.task_id) {
            setSwarmEvent({ taskId: data.task_id, type: "completed" });
            setTimeout(() => setSwarmEvent(null), 3000);
            if (!dropL3StreamUntilTerminalRef.current) {
              onStepRef.current?.(
                "task_completed",
                formatSensoryDebugBlock(data, "swarm", `### Swarm · 任务完成\n- task_id: ${data.task_id}`),
                data.run_id ?? "",
              );
            }
          }
        } catch {
          // 忽略非 JSON 消息
        }
      };

      ws.onclose = () => {
        setConnected(false);
        wsRef.current = null;
        reconnectTimerRef.current = setTimeout(connect, RECONNECT_DELAY_MS);
      };

      ws.onerror = () => {
        ws.close();
      };
    } catch {
      setConnected(false);
      reconnectTimerRef.current = setTimeout(connect, RECONNECT_DELAY_MS);
    }
  }, []);

  const disconnect = useCallback(() => {
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    wsRef.current?.close();
    wsRef.current = null;
    setConnected(false);
    setLastPayload(null);
    setHitlPending(null);
    streamingAccRef.current = "";
    hadStreamChunksForRunRef.current = false;
    dropL3StreamUntilTerminalRef.current = false;
    setStreamChunkKind(null);
    setStreamingContent("");
    setCurrentRunId(null);
    setMemoryCompactSuggest(null);
    setZombieTasksPending(null);
  }, []);

  const sendHitlResponse = useCallback((approved: boolean, taskId?: string) => {
    const tid = taskId ?? hitlPending?.task_id;
    if (!tid || wsRef.current?.readyState !== WebSocket.OPEN) return;
    const action = approved ? "HITL_APPROVE" : "HITL_REJECT";
    wsRef.current.send(JSON.stringify({ action, task_id: tid }));
    setHitlPending(null);
  }, [hitlPending?.task_id]);

  /** 与本地 /clear 配合：清空 L3 侧会话缓冲（非 intent，不经大模型）；含 Lark chat_id 时同步落盘空会话 */
  const sendSessionClearControl = useCallback(() => {
    if (wsRef.current?.readyState !== WebSocket.OPEN) return false;
    const payload: Record<string, string> = { type: "clear_session" };
    if (larkChatId) {
      payload.chat_id = larkChatId;
      payload.session_id = larkChatId;
    } else {
      const sid = desktopSessionIdRef?.current?.trim() ?? "";
      if (sid) {
        payload.chat_id = sid;
        payload.session_id = sid;
      }
    }
    wsRef.current.send(JSON.stringify(payload));
    return true;
  }, [larkChatId, desktopSessionIdRef]);

  /**
   * 语音预热控制帧：麦克风刚进入 listening 即触发，
   * 让 L3 在后台提前加载会话历史/摘要，隐藏后续首包延迟。
   */
  const sendPrepareContextControl = useCallback((trigger: "ptt_start" | "companion_voice_start" = "ptt_start") => {
    if (wsRef.current?.readyState !== WebSocket.OPEN) return false;
    const payload: Record<string, string> = {
      type: "prepare_context",
      trigger,
      source: "desktop_voice",
    };
    if (larkChatId) {
      payload.chat_id = larkChatId;
      payload.session_id = larkChatId;
    } else {
      const sid = desktopSessionIdRef?.current?.trim() ?? "";
      if (sid) {
        payload.chat_id = sid;
        payload.session_id = sid;
      }
    }
    wsRef.current.send(JSON.stringify(payload));
    return true;
  }, [larkChatId, desktopSessionIdRef]);

  const resolveHitl = useCallback((approved: boolean) => {
    const tid = hitlPending?.task_id;
    sendHitlResponse(approved, tid);
  }, [sendHitlResponse, hitlPending?.task_id]);

  /** 发送聊天输入到 Layer 3（与 v0.8.98 一致：`{ intent }`；L3 ws_server 读 intent/content） */
  const sendInput = useCallback(
    (
      text: string,
      extras?: {
        attachments_metadata?: Array<{
          name: string;
          size_bytes: number;
          mime: string;
          has_image: boolean;
          base64: string;
        }>;
        implicit_signals?: Record<string, unknown>;
      },
    ) => {
      dropL3StreamUntilTerminalRef.current = false;
      const intentTrim = text.trim();
      const hasAtt = (extras?.attachments_metadata?.length ?? 0) > 0;
      if (!intentTrim && !hasAtt) {
        console.debug("[Sensory] sendInput 跳过: 空文本且无附件");
        return false;
      }
      if (wsRef.current?.readyState !== WebSocket.OPEN) {
        console.debug("[Sensory] sendInput 失败: ws 未连接 readyState=%s", wsRef.current?.readyState ?? "null");
        return false;
      }
      const payload: Record<string, unknown> = {
        intent: intentTrim || (hasAtt ? "请查看附件并回答。" : ""),
      };
      if (extras?.attachments_metadata?.length) {
        payload.attachments_metadata = extras.attachments_metadata;
      }
      if (extras?.implicit_signals && Object.keys(extras.implicit_signals).length > 0) {
        payload.implicit_signals = extras.implicit_signals;
      }
      if (larkChatId) {
        payload.chat_id = larkChatId;
        payload.session_id = larkChatId;
        payload.origin = "terminal";
      } else {
        const sid = desktopSessionIdRef?.current?.trim() ?? "";
        if (sid) {
          payload.chat_id = sid;
          payload.session_id = sid;
        }
      }
      wsRef.current.send(JSON.stringify(payload));
      onUserInputRef.current?.(intentTrim, { source: "local" });
      console.debug(
        "[Sensory] sendInput 已发送 len=%d attachments=%s mirror=%s",
        intentTrim.length,
        extras?.attachments_metadata?.length ?? 0,
        !!larkChatId,
      );
      return true;
    },
    [larkChatId, desktopSessionIdRef],
  );

  /**
   * 生成式 UI：将用户在面板中确认的参数发给 L3（ws_server `tool_ui_result`），
   * 由 Native 工具执行后通过 answer 帧回传正文。
   */
  const sendToolUiResult = useCallback(
    (p: { toolName: string; toolCallId?: string; result: unknown }) => {
      if (wsRef.current?.readyState !== WebSocket.OPEN) {
        console.warn("[Sensory] sendToolUiResult: WebSocket 未连接");
        return false;
      }
      const payload: Record<string, unknown> = {
        type: "tool_ui_result",
        tool_name: p.toolName,
        result: p.result,
      };
      if (p.toolCallId) payload.tool_call_id = p.toolCallId;
      if (larkChatId) {
        payload.chat_id = larkChatId;
        payload.session_id = larkChatId;
      } else {
        const sid = desktopSessionIdRef?.current?.trim() ?? "";
        if (sid) {
          payload.chat_id = sid;
          payload.session_id = sid;
        }
      }
      wsRef.current.send(JSON.stringify(payload));
      return true;
    },
    [larkChatId, desktopSessionIdRef],
  );

  useEffect(() => {
    connect();
    return () => disconnect();
  }, [connect, disconnect]);

  /** 记忆整理提示：每秒倒计时，归零时发 auto_start 并收起横幅 */
  useEffect(() => {
    if (!memoryCompactSuggest || memoryCompactSuggest.remainingSec <= 0) return;
    const id = window.setTimeout(() => {
      setMemoryCompactSuggest((prev) => {
        if (!prev) return null;
        const n = prev.remainingSec - 1;
        if (n <= 0) {
          if (wsRef.current?.readyState === WebSocket.OPEN) {
            wsRef.current.send(JSON.stringify({ type: "memory_compact_auto_start" }));
          }
          return null;
        }
        return { ...prev, remainingSec: n };
      });
    }, 1000);
    return () => clearTimeout(id);
  }, [memoryCompactSuggest]);

  // Lark 镜像：连接后若 larkChatId 有值则订阅；larkChatId 变化时若已连接则更新订阅
  useEffect(() => {
    if (!larkChatId || wsRef.current?.readyState !== WebSocket.OPEN) return;
    wsRef.current.send(JSON.stringify({ type: "subscribe_mirror", lark_chat_id: larkChatId }));
  }, [larkChatId, connected]);

  /** 重连后 onopen 会发 subscribe；此处兜底确保已连上时也会订阅后台任务 */
  useEffect(() => {
    if (!connected || wsRef.current?.readyState !== WebSocket.OPEN) return;
    wsRef.current.send(JSON.stringify({ type: "subscribe_background_tasks" }));
  }, [connected]);

  return {
    connected,
    lastPayload,
    hitlPending,
    resolveHitl,
    sendHitlResponse,
    reconnect: connect,
    /** 发送聊天输入到 Layer 3 */
    sendInput,
    /** 生成式 UI 工具参数 → L3 Native 执行 */
    sendToolUiResult,
    /** 通知 L3 清空 WS 会话缓冲（控制帧，非用户 intent） */
    sendSessionClearControl,
    /** 语音开始时触发：让 L3 预热会话上下文 */
    sendPrepareContextControl,
    /** 停止当前 L3 生成（发 run_abort + 丢弃残余流直至终结帧） */
    sendRunAbort,
    /** Lark 镜像：注册回调，Lark 用户发消息时终端同步显示 */
    registerMirrorInputHandler,
    registerBackgroundTaskHandler,
    registerUserInputHandler,
    /** 是否处于 Lark 镜像模式 */
    larkMirrorMode: !!larkChatId,
    // v8.0 视觉觉醒
    streamingContent,
    streamChunkKind,
    currentRunId,
    handoffEvent,
    swarmEvent,
    registerChunkHandler,
    registerAnswerHandler,
    registerStepHandler,
    memoryCompactSuggest,
    dismissMemoryCompactSuggest,
    sendMemoryCompactControl,
    /** 断电遗留后台任务横幅（L3 zombie_tasks_pending） */
    zombieTasksPending,
    dismissZombieTasksPending,
    /** 后台任务单行进度（桌面聊天输入区上方展示） */
    backgroundTaskPulse,
  };
}
