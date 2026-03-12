/**
 * UISyncProvider - L2 SSE 连接 + 全局 Toast
 *
 * 包裹在 App 或 ConsoleApp 根组件，建立对 L2 /api/v2/events/ui-sync 的 EventSource 监听。
 * 收到 INVENTORY_UPDATED 时显示 Toast，并派发 inventory-updated 事件供技能面板刷新。
 */

import { ReactNode } from "react";
import { useUISyncEventSource } from "../hooks/useUISyncEventSource";
import { InventoryToast } from "./InventoryToast";

export function UISyncProvider({ children }: { children: ReactNode }) {
  const { toastVisible, dismissToast } = useUISyncEventSource();

  return (
    <>
      {children}
      <InventoryToast visible={toastVisible} onDismiss={dismissToast} />
    </>
  );
}
