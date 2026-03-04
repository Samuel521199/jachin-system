/**
 * useSensoryWebSocket - Layer 3 全息感官总线连接
 * 连接 ws://localhost:8080/sensory，接收大脑 step_type / thought / action / HITL_REQUIRED
 */

import { useState, useEffect, useCallback, useRef } from "react";

const SENSORY_WS_URL = "ws://localhost:8080/sensory";
const RECONNECT_DELAY_MS = 3000;

export interface SensoryPayload {
  step_type: string;
  content: string;
  source?: string;
  task_id?: string;
}

export function useSensoryWebSocket() {
  const [connected, setConnected] = useState(false);
  const [lastPayload, setLastPayload] = useState<SensoryPayload | null>(null);
  const [hitlPending, setHitlPending] = useState<SensoryPayload | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    try {
      const ws = new WebSocket(SENSORY_WS_URL);
      wsRef.current = ws;

      ws.onopen = () => {
        setConnected(true);
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data) as SensoryPayload;
          setLastPayload(data);

          if (data.step_type === "HITL_REQUIRED") {
            setHitlPending(data);
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
  };
}
