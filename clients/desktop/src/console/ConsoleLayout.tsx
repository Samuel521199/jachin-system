/**
 * ConsoleLayout - HUD 指挥中心主布局
 * 深空背景 + 网格动画 + 悬浮玻璃侧栏 + 顶部状态栏 (Horizon) + 沉浸式内容区
 * 快捷键: Ctrl+Shift+E 切换控制台显示, Ctrl+Shift+C 打开聊天
 */

import { useState, useEffect, useCallback } from "react";
import { Outlet } from "react-router-dom";
import { invoke } from "@tauri-apps/api/core";
import { getMemoryCount, getConfig, getDevices } from "../lib/api";
import { useSpriteStore } from "../store/spriteStore";
import { Sidebar } from "./Sidebar";
import { Horizon } from "./components/Horizon";
import { VoidBackground } from "./components/VoidBackground";

export function ConsoleLayout() {
  const themeId = useSpriteStore((s) => s.themeId);
  const [voidData, setVoidData] = useState<{
    memoryCount: number;
    deviceCount: number;
  } | null>(null);
  const [config, setConfig] = useState<{ environment?: string; model_name?: string } | null>(null);

  const fetchVoidData = useCallback(async () => {
    try {
      const [memRes, devices] = await Promise.all([
        getMemoryCount(),
        getDevices(),
      ]);
      const memCount = memRes?.count ?? 0;
      const devCount = devices?.length ?? 0;
      const total = memCount + devCount;
      setVoidData(total > 0 ? { memoryCount: memCount, deviceCount: devCount } : null);
    } catch {
      setVoidData(null);
    }
  }, []);

  const fetchConfig = useCallback(async () => {
    try {
      const c = await getConfig();
      setConfig({ environment: c.environment, model_name: c.model_name });
    } catch {
      setConfig(null);
    }
  }, []);

  useEffect(() => {
    fetchVoidData();
    const t1 = setInterval(fetchVoidData, 15000);
    return () => clearInterval(t1);
  }, [fetchVoidData]);

  useEffect(() => {
    fetchConfig();
    const t2 = setInterval(fetchConfig, 20000);
    return () => clearInterval(t2);
  }, [fetchConfig]);

  // 快捷键: Ctrl+Shift+E 切换控制台, Ctrl+Shift+C 打开聊天
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.ctrlKey && e.shiftKey) {
        if (e.key === "E") {
          e.preventDefault();
          void invoke("quick_action_eagle_eye");
        } else if (e.key === "C") {
          e.preventDefault();
          void invoke("show_chat_window");
        }
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  return (
    <div
      className="relative h-screen w-screen flex overflow-hidden text-white console-deep-space"
      data-theme={themeId}
    >
      <VoidBackground
        memoryCount={voidData?.memoryCount}
        deviceCount={voidData?.deviceCount}
      />
      <Sidebar />
      <main className="flex-1 min-w-0 flex flex-col overflow-hidden">
        <Horizon
          environment={config?.environment ?? import.meta.env.VITE_ENVIRONMENT ?? undefined}
          modelName={config?.model_name ?? import.meta.env.VITE_MODEL_NAME ?? undefined}
        />
        <div className="flex-1 min-h-0 overflow-auto flex flex-col">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
