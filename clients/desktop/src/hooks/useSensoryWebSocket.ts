/**
 * useSensoryWebSocket - Layer 3 全息感官总线连接
 * 连接 ws://localhost:18881/sensory，接收大脑 step_type / thought / action / HITL_REQUIRED
 * v8.0 视觉觉醒：stream_chunk 流式神经、handoff 人格切换、swarm 算力雷达
 */

import { useState, useEffect, useCallback, useRef } from "react";

const SENSORY_WS_PORT = import.meta.env.VITE_SENSORY_WS_PORT || "18881";
const SENSORY_WS_URL = `ws://localhost:${SENSORY_WS_PORT}/sensory`;
const RECONNECT_DELAY_MS = 3000;

/** v8.0 能力协商：声明 stream_chunk 以接收逐 token 推送 */
const MANIFEST_CAPS = ["ui_render", "hitl_popup", "stream_chunk"];

export interface SensoryPayload {
  step_type: string;
  content: string;
  source?: string;
  task_id?: string;
  run_id?: string;
  tool?: string;
  payload?: Record<string, unknown>;
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

export function useSensoryWebSocket() {
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
  const onAnswerRef = useRef<((content: string) => void) | null>(null);

  /** 注册 chunk 回调：供 Chat 将流式内容追加到当前 Assistant 消息 */
  const registerChunkHandler = useCallback((fn: ((chunk: string, runId: string) => void) | null) => {
    onChunkRef.current = fn;
  }, []);

  /** 注册 answer 回调：收到最终回复时调用（含完整内容，用于无 chunk 时的兜底） */
  const registerAnswerHandler = useCallback((fn: ((content: string) => void) | null) => {
    onAnswerRef.current = fn;
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
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data) as SensoryPayload;
          setLastPayload(data);

          if (data.step_type === "HITL_REQUIRED") {
            setHitlPending(data);
          }

          // v8.0 流式神经：chunk 追加到当前消息，不创建新气泡
          if (data.step_type === "chunk" && data.content != null) {
            const runId = data.run_id ?? "";
            setStreamingContent((prev) => prev + data.content);
            setCurrentRunId(runId);
            onChunkRef.current?.(data.content, runId);
          }

          // answer/rejected/error 结束时：通知回调（兜底完整内容），再清空流式状态
          if (["answer", "rejected", "error"].includes(data.step_type)) {
            const content = data.content ?? "";
            onAnswerRef.current?.(content);
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

  /** 发送聊天输入到 Layer 2（通过 Sensory WebSocket，注入全息感官总线） */
  const sendInput = useCallback((text: string) => {
    if (!text.trim() || wsRef.current?.readyState !== WebSocket.OPEN) return false;
    wsRef.current.send(JSON.stringify({ type: "input", intent: text.trim() }));
    return true;
  }, []);

  useEffect(() => {
    connect();
    return () => disconnect();
  }, [connect, disconnect]);

  return {
    connected,
    lastPayload,
    hitlPending,
    resolveHitl,
    sendHitlResponse,
    reconnect: connect,
    /** 发送聊天输入到 Layer 2 */
    sendInput,
    // v8.0 视觉觉醒
    streamingContent,
    currentRunId,
    handoffEvent,
    swarmEvent,
    registerChunkHandler,
    registerAnswerHandler,
  };
}
