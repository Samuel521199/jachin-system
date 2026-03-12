"use client";

import React, { Component, ErrorInfo, ReactNode } from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

/**
 * Jachin 风格全局错误捕获
 * 当 API 返回异常或数据渲染崩溃时，展示占位页而非白屏
 */
export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error("[ErrorBoundary] 渲染崩溃:", error, errorInfo);
  }

  handleRetry = () => {
    this.setState({ hasError: false, error: null });
  };

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback;

      return (
        <div
          className="min-h-screen flex flex-col items-center justify-center bg-[#030712] text-white"
          style={{
            background: `
              linear-gradient(rgba(255,255,255,0.02) 1px, transparent 1px),
              linear-gradient(90deg, rgba(255,255,255,0.02) 1px, transparent 1px)
            `,
            backgroundSize: "48px 48px",
          }}
        >
          <div
            className="absolute inset-0 -z-10"
            style={{
              background: `
                radial-gradient(ellipse 100% 80% at 50% 0%, rgba(236, 72, 153, 0.08) 0%, transparent 50%),
                radial-gradient(ellipse 80% 60% at 100% 80%, rgba(139, 92, 246, 0.06) 0%, transparent 50%)
              `,
            }}
          />
          <div className="rounded-2xl border border-white/10 bg-white/[0.02] backdrop-blur-xl p-10 max-w-md text-center">
            <div className="rounded-full bg-rose-500/20 p-4 inline-flex mb-6">
              <AlertTriangle className="h-12 w-12 text-rose-400" />
            </div>
            <h1 className="text-xl font-bold text-white mb-2 tracking-tight">
              出了点小问题
            </h1>
            <p className="text-white/60 text-sm mb-6">
              {this.state.error?.message || "页面渲染异常，请重试"}
            </p>
            <button
              onClick={this.handleRetry}
              className="inline-flex items-center gap-2 px-6 py-3 rounded-xl bg-cyan-500/20 text-cyan-300 border border-cyan-400/40 hover:bg-cyan-500/30 transition-colors font-medium"
            >
              <RefreshCw className="h-4 w-4" />
              重试
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
