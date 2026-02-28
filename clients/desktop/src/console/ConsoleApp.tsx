/**
 * ConsoleApp - 控制台应用入口
 * 使用 HashRouter 适配 Tauri 单页与本地加载
 * 含 ErrorBoundary，出错时显示错误信息而非空白
 */

import React, { Component, ErrorInfo, ReactNode } from "react";
import { RouterProvider } from "react-router-dom";
import { consoleRouter } from "./routes";

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

  render() {
    if (this.state.hasError && this.state.error) {
      return (
        <div className="h-screen w-screen flex items-center justify-center bg-slate-900 text-white p-8">
          <div className="max-w-2xl space-y-4">
            <h1 className="text-xl font-bold text-rose-400">控制台加载异常</h1>
            <pre className="font-mono text-sm text-slate-300 bg-black/30 p-4 rounded overflow-auto max-h-64">
              {this.state.error.message}
            </pre>
            <p className="text-slate-500 text-sm">
              请检查控制台 (F12) 或后端日志以获取更多信息。刷新页面可重试。
            </p>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

export function ConsoleApp() {
  return (
    <ConsoleErrorBoundary>
      <RouterProvider router={consoleRouter} />
    </ConsoleErrorBoundary>
  );
}
