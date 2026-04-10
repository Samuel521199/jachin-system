/**
 * ConsoleApp - 控制台应用入口（main / console.html）
 * OMNI 独立对话窗为 chat.html，侧栏 Skill 画布布局见 `src/chat.tsx`（非本文件）。
 * 使用 HashRouter 适配 Tauri 单页与本地加载
 * 含 ErrorBoundary，出错时显示错误信息而非空白
 * V2: 未配对时显示 L2 网关接驳 GatewayConnectScreen
 */

import React, { Component, ErrorInfo, ReactNode, useState, useEffect } from "react";
import { RouterProvider } from "react-router-dom";
import { invoke } from "@tauri-apps/api/core";
import { consoleRouter } from "./routes";
import { GatewayConnectScreen } from "../components/GatewayConnectScreen";
import { UISyncProvider } from "../components/UISyncProvider";

class ConsoleErrorBoundary extends Component<
  { children: ReactNode },
  { hasError: boolean; error: Error | null }
> {
  state = { hasError: false, error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("[Console] ErrorBoundary caught:", error, info);
  }

  handleRetry = () => {
    this.setState({ hasError: false, error: null });
  };

  render() {
    if (this.state.hasError && this.state.error) {
      return (
        <div className="h-screen w-screen flex items-center justify-center bg-slate-950 text-white p-8">
          <div
            className="rounded-2xl border border-white/10 bg-slate-900/80 backdrop-blur-xl p-10 max-w-md text-center"
            style={{
              boxShadow: "0 0 40px rgba(139, 92, 246, 0.15)",
            }}
          >
            <div className="rounded-full bg-rose-500/20 p-4 inline-flex mb-6">
              <svg
                className="h-12 w-12 text-rose-400"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"
                />
              </svg>
            </div>
            <h1 className="text-xl font-bold text-white mb-2 tracking-tight">
              出了点小问题
            </h1>
            <p className="text-slate-400 text-sm mb-6">
              {this.state.error.message || "页面渲染异常，请重试"}
            </p>
            <button
              onClick={this.handleRetry}
              className="inline-flex items-center gap-2 px-6 py-3 rounded-xl bg-cyan-500/20 text-cyan-300 border border-cyan-400/40 hover:bg-cyan-500/30 transition-colors font-medium"
            >
              <svg
                className="h-4 w-4"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
                />
              </svg>
              重试
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

export function ConsoleApp() {
  const [paired, setPaired] = useState<boolean | null>(null);

  useEffect(() => {
    invoke<boolean>("is_gateway_paired")
      .then(setPaired)
      .catch(() => setPaired(false));
  }, []);

  if (paired === null) {
    return (
      <div className="h-screen w-screen flex items-center justify-center bg-slate-950 text-white">
        <div className="animate-pulse text-cyan-400/80">Resuming...</div>
      </div>
    );
  }

  if (!paired) {
    return (
      <ConsoleErrorBoundary>
        <GatewayConnectScreen onPaired={() => setPaired(true)} />
      </ConsoleErrorBoundary>
    );
  }

  return (
    <ConsoleErrorBoundary>
      <UISyncProvider>
        <RouterProvider router={consoleRouter} />
      </UISyncProvider>
    </ConsoleErrorBoundary>
  );
}
