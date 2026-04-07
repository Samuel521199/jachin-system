/**
 * useSensoryWebSocket - Layer 3 全息感官总线连接
 * 连接 ws://localhost:18981/sensory，接收大脑 step_type / thought / action / HITL_REQUIRED
 * v8.0 视觉觉醒：stream_chunk 流式神经、handoff 人格切换、swarm 算力雷达
 */

import { useState, useEffect, useCallback, useRef } from "react";
import { formatAssistantStepPayload } from "../utils/sensoryStepFormat";
import { mergeStreamChunk } from "../utils/streamChunkMerge";

const SENSORY_WS_PORT = import.meta.env.VITE_SENSORY_WS_PORT || "18981";
const SENSORY_WS_HOST = import.meta.env.VITE_SENSORY_WS_HOST || "127.0.0.1";
const SENSORY_WS_URL = `ws://${SENSORY_WS_HOST}:${SENSORY_WS_PORT}/sensory`;
const RECONNECT_DELAY_MS = 3000;

/** Lark 镜像模式：从 VITE_LARK_CHAT_ID 或参数传入，终端作为主屏、Lark 为副屏同步显示 */
const DEFAULT_LARK_CHAT_ID = import.meta.env.VITE_LARK_CHAT_ID || "";

/** v8.0 能力协商：声明 stream_chunk 以接收逐 token 推送 */
const MANIFEST_CAPS = ["ui_render", "hitl_popup", "stream_chunk"];

export interface UseSensoryOptions {
  /** Lark chat_id：启用镜像模式，Lark 消息同步到终端，终端回复同步到 Lark */
  larkChatId?: string;
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

/** 随 answer 回调：用于区分「仅有流式拼气泡」与「无 chunk 时由 step 注入 ### 回复」；runId 对齐 L3 WS 防超时后陈旧 answer 污染新气泡 */
export interface SensoryAnswerMeta {
  hadStreamChunks?: boolean;
  runId?: string;
}

export function useSensoryWebSocket(options: UseSensoryOptions = {}) {
  const larkChatId = (options.larkChatId ?? DEFAULT_LARK_CHAT_ID).trim();
  const [connected, setConnected] = useState(false);
  const [lastPayload, setLastPayload] = useState<SensoryPayload | null>(null);
  const [hitlPending, setHitlPending] = useState<SensoryPayload | null>(null);
  /** v8.0 流式神经：当前 run 的累积内容，收到 answer 时清空 */
  const [streamingContent, setStreamingContent] = useState("");
  const [currentRunId, setCurrentRunId] = useState<string | null>(null);
  /** Handoff 人格切换：供 UI 触发主题色突变 */
  const [handoffEvent, setHandoffEvent] = useState<HandoffEvent | null>(null);
  /** Swarm 算力雷达：task_offer 时显示扫描，task_completed 时爆发 */
  const [swarmEvent, setSwarmEvent] = useState<SwarmEvent | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const onChunkRef = useRef<((chunk: string, runId: string) => void) | null>(null);
  const onAnswerRef = useRef<((content: string, meta?: SensoryAnswerMeta) => void) | null>(null);
  const onStepRef = useRef<((stepType: string, content: string, runId?: string) => void) | null>(null);
  const onMirrorInputRef = useRef<((content: string) => void) | null>(null);
  /** 本轮是否已收到流式 chunk（有则 answer 勿再向同气泡追加全文，否则会「复读机」） */
  const hadStreamChunksForRunRef = useRef(false);
  /** 与 streamingContent 同步，用于合并 cumulative/delta chunk，避免重复拼接 */
  const streamingAccRef = useRef("");

  /** 注册 Lark 镜像输入回调：Lark 用户发消息时，终端同步显示 */
  const registerMirrorInputHandler = useCallback((fn: ((content: string) => void) | null) => {
    onMirrorInputRef.current = fn;
  }, []);

  /** 注册 chunk 回调：供 Chat 将流式内容追加到当前 Assistant 消息 */
  const registerChunkHandler = useCallback((fn: ((chunk: string, runId: string) => void) | null) => {
    onChunkRef.current = fn;
  }, []);

  /** 注册 answer 回调：收到最终回复时调用（content 与 Lark 推送同源；meta.hadStreamChunks 表示本轮曾收到 chunk） */
  const registerAnswerHandler = useCallback((fn: ((content: string, meta?: SensoryAnswerMeta) => void) | null) => {
    onAnswerRef.current = fn;
  }, []);

  /** 注册 step 回调：thought/action/observation 等思考过程，供 Chat 完整展示 */
  const registerStepHandler = useCallback((fn: ((stepType: string, content: string, runId?: string) => void) | null) => {
    onStepRef.current = fn;
  }, []);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    try {
      const ws = new WebSocket(SENSORY_WS_URL);
      wsRef.current = ws;

      ws.onopen = () => {
        setConnected(true);
        // v8.0 能力协商：声明 stream_chunk 以接收逐 token 推送
        ws.send(JSON.stringify({ type: "manifest", caps: MANIFEST_CAPS }));
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
          const step =
            (typeof raw.step_type === "string" && raw.step_type) ||
            (typeof raw.action_type === "string" && raw.action_type) ||
            (typeof raw.type === "string" && raw.type) ||
            "";
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
          }

          if (data.step_type === "HITL_REQUIRED") {
            setHitlPending(data);
          }

          // v8.0 流式神经：合并 chunk（支持全量累加或纯增量），仅把新增 delta 交给 Chat 气泡
          if (data.step_type === "chunk" && data.content != null) {
            const runId = data.run_id ?? "";
            const { next, delta } = mergeStreamChunk(streamingAccRef.current, data.content);
            streamingAccRef.current = next;
            setStreamingContent(next);
            setCurrentRunId(runId);
            if (delta) {
              hadStreamChunksForRunRef.current = true;
              onChunkRef.current?.(delta, runId);
            }
          }

          // thought/action/observation：完整展示思考过程，禁止总结
          if (["thought", "action", "observation"].includes(data.step_type) && data.content != null) {
            const labels: Record<string, string> = { thought: "思考", action: "动作", observation: "观察" };
            const label = labels[data.step_type] ?? data.step_type;
            onStepRef.current?.(data.step_type, `### ${label}\n\n${data.content}\n\n`, data.run_id ?? "");
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
            onStepRef.current?.("system_status", `### 系统状态\n\n${line}\n\n`, data.run_id ?? "");
          }

          // answer/rejected/error 结束时：先追加到思考过程，再通知回调
          if (["answer", "rejected", "error"].includes(data.step_type)) {
            const content = data.content ?? "";
            const hadChunks = hadStreamChunksForRunRef.current;
            hadStreamChunksForRunRef.current = false;
            streamingAccRef.current = "";
            // 流式已逐段拼进气泡时，禁止再注入整段正文（否则与 chunk 叠加成双倍/多倍复读）
            const stepPayload = hadChunks
              ? ""
              : formatAssistantStepPayload(data.step_type, content);
            onStepRef.current?.(data.step_type, stepPayload, data.run_id ?? "");
            onAnswerRef.current?.(content, {
              hadStreamChunks: hadChunks,
              runId: data.run_id ?? "",
            });
            setStreamingContent("");
            setCurrentRunId(null);
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

          // v8.0 Swarm：task_offer 显示雷达扫描
          if (data.step_type === "task_offer" && data.task_id) {
            setSwarmEvent({ taskId: data.task_id, type: "offer", tool: data.tool });
          }
          if (data.step_type === "task_assigned" && data.task_id) {
            setSwarmEvent({ taskId: data.task_id, type: "assigned", tool: data.tool });
          }
          if (data.step_type === "task_completed" && data.task_id) {
            setSwarmEvent({ taskId: data.task_id, type: "completed" });
            setTimeout(() => setSwarmEvent(null), 3000);
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
    setStreamingContent("");
    setCurrentRunId(null);
  }, []);

  const sendHitlResponse = useCallback((approved: boolean, taskId?: string) => {
    const tid = taskId ?? hitlPending?.task_id;
    if (!tid || wsRef.current?.readyState !== WebSocket.OPEN) return;
    const action = approved ? "HITL_APPROVE" : "HITL_REJECT";
    wsRef.current.send(JSON.stringify({ action, task_id: tid }));
    setHitlPending(null);
  }, [hitlPending?.task_id]);

  const resolveHitl = useCallback((approved: boolean) => {
    const tid = hitlPending?.task_id;
    sendHitlResponse(approved, tid);
  }, [sendHitlResponse, hitlPending?.task_id]);

  /** 发送聊天输入到 Layer 3（与 v0.8.98 一致：`{ intent }`；L3 ws_server 读 intent/content） */
  const sendInput = useCallback((text: string) => {
    if (!text.trim()) {
      console.debug("[Sensory] sendInput 跳过: 空文本");
      return false;
    }
    if (wsRef.current?.readyState !== WebSocket.OPEN) {
      console.debug("[Sensory] sendInput 失败: ws 未连接 readyState=%s", wsRef.current?.readyState ?? "null");
      return false;
    }
    const payload: Record<string, string> = { intent: text.trim() };
    if (larkChatId) {
      payload.chat_id = larkChatId;
      payload.origin = "terminal";
    }
    wsRef.current.send(JSON.stringify(payload));
    console.debug("[Sensory] sendInput 已发送 len=%d mirror=%s", text.trim().length, !!larkChatId);
    return true;
  }, [larkChatId]);

  useEffect(() => {
    connect();
    return () => disconnect();
  }, [connect, disconnect]);

  // Lark 镜像：连接后若 larkChatId 有值则订阅；larkChatId 变化时若已连接则更新订阅
  useEffect(() => {
    if (!larkChatId || wsRef.current?.readyState !== WebSocket.OPEN) return;
    wsRef.current.send(JSON.stringify({ type: "subscribe_mirror", lark_chat_id: larkChatId }));
  }, [larkChatId, connected]);

  return {
    connected,
    lastPayload,
    hitlPending,
    resolveHitl,
    sendHitlResponse,
    reconnect: connect,
    /** 发送聊天输入到 Layer 3 */
    sendInput,
    /** Lark 镜像：注册回调，Lark 用户发消息时终端同步显示 */
    registerMirrorInputHandler,
    /** 是否处于 Lark 镜像模式 */
    larkMirrorMode: !!larkChatId,
    // v8.0 视觉觉醒
    streamingContent,
    currentRunId,
    handoffEvent,
    swarmEvent,
    registerChunkHandler,
    registerAnswerHandler,
    registerStepHandler,
  };
}
