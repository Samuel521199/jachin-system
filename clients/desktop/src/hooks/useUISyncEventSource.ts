/**
 * useUISyncEventSource - L2 SSE 实时 UI 同步
 *
 * 连接 L2 GET /api/v2/events/ui-sync，监听 INVENTORY_UPDATED 等事件。
 * 收到事件时：显示 Toast、派发自定义事件供技能面板刷新。
 *
 * EventSource 具备原生断线重连能力，error 时自动重连；组件卸载时 close() 防止内存泄漏。
 */

import { useEffect, useState, useCallback, useRef } from "react";
import { BACKEND_URL } from "../lib/api";

export const INVENTORY_UPDATED_EVENT = "inventory-updated";

export interface UISyncEvent {
  event?: string;
  type: string;
  message: string;
  timestamp?: string;
  mcps_injected?: number;
  skills_found?: number;
  data?: { message?: string; timestamp?: string; [k: string]: unknown };
}

function getEventType(data: UISyncEvent): string {
  return data.event ?? data.type ?? "";
}

export function useUISyncEventSource() {
  const [connected, setConnected] = useState(false);
  const [lastEvent, setLastEvent] = useState<UISyncEvent | null>(null);
  const [toastVisible, setToastVisible] = useState(false);
  const wasConnectedRef = useRef(false);

  const handleEvent = useCallback((event: UISyncEvent) => {
    setLastEvent(event);
    const evType = getEventType(event);
    if (evType === "INVENTORY_UPDATED") {
      setToastVisible(true);
      window.dispatchEvent(new CustomEvent(INVENTORY_UPDATED_EVENT, { detail: event }));
      setTimeout(() => setToastVisible(false), 4000);
    }
  }, []);

  useEffect(() => {
    const url = `${BACKEND_URL}/api/v2/events/ui-sync`;
    const es = new EventSource(url);

    es.onopen = () => {
      setConnected(true);
      if (wasConnectedRef.current) {
        // 断线重连后，主动触发刷新确保状态同步
        window.dispatchEvent(new CustomEvent(INVENTORY_UPDATED_EVENT, { detail: { type: "RECONNECTED" } }));
      }
      wasConnectedRef.current = true;
    };

    es.onerror = () => {
      setConnected(false);
      // EventSource 会自动重连，无需手动处理
    };

    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data) as UISyncEvent;
        const evType = getEventType(data);
        if (evType === "CONNECTED") {
          setConnected(true);
        } else {
          handleEvent(data);
        }
      } catch {
        // 忽略解析失败（如 heartbeat 注释行）
      }
    };

    return () => {
      es.close();
      setConnected(false);
    };
  }, [handleEvent]);

  const dismissToast = useCallback(() => setToastVisible(false), []);

  return { connected, lastEvent, toastVisible, dismissToast };
}
