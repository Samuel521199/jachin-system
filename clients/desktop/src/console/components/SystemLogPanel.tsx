/**
 * SystemLogPanel - L3 全息监控终端
 *
 * 订阅 GET /api/system/logs/stream SSE，将调度器、锁竞争、任务成败等日志
 * 实时展示到控制台面板，按级别着色（INFO/SUCCESS/WARNING/ERROR），自动滚动到底部。
 * 使用 fetch+ReadableStream 替代 EventSource，规避 Tauri WebView 跨域限制。
 */

import { useEffect, useRef, useState, useCallback } from "react";
import { ChevronDown, ChevronUp, RefreshCw, Terminal } from "lucide-react";
import { getL3LogsStreamUrls } from "../../lib/api";

export interface LogEntry {
  message: string;
  level: string;
  ts: number;
}

const LEVEL_COLORS: Record<string, string> = {
  INFO: "text-slate-400",
  SUCCESS: "text-emerald-400",
  WARNING: "text-amber-400",
  ERROR: "text-rose-400",
};

function levelColor(level: string): string {
  return LEVEL_COLORS[level?.toUpperCase()] ?? "text-slate-400";
}

function parseSSELine(line: string): { message?: string; level?: string; ts?: number } | null {
  if (line.startsWith("data: ")) {
    try {
      return JSON.parse(line.slice(6)) as { message?: string; level?: string; ts?: number };
    } catch {
      return null;
    }
  }
  return null;
}

export function SystemLogPanel() {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [collapsed, setCollapsed] = useState(false);
  const [connected, setConnected] = useState(false);
  const [retryKey, setRetryKey] = useState(0);
  const scrollRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  const scrollToBottom = useCallback(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [logs, scrollToBottom]);

  useEffect(() => {
    const urls = getL3LogsStreamUrls();
    let retryId: ReturnType<typeof setTimeout> | null = null;
    let urlIndex = 0;
    let retryCount = 0;
    let mounted = true;

    const onData = (data: { message?: string; level?: string; ts?: number }) => {
      const msg = data?.message ?? "";
      const level = (data?.level ?? "INFO").toUpperCase();
      const ts = typeof data?.ts === "number" ? data.ts : Date.now() / 1000;
      if (msg && mounted) {
        setLogs((prev) => {
          const next = [...prev, { message: msg, level, ts }];
          return next.slice(-500);
        });
      }
    };

    const connectFetch = async () => {
      if (!mounted) return;
      if (urlIndex >= urls.length) {
        urlIndex = 0;
        retryCount++;
        const backoff = Math.min(2000 + retryCount * 1000, 6000);
        retryId = setTimeout(connectFetch, backoff);
        return;
      }
      const url = urls[urlIndex];
      abortRef.current?.abort();
      abortRef.current = new AbortController();
      try {
        const res = await fetch(url, {
          signal: abortRef.current.signal,
          cache: "no-store",
          mode: "cors",
          credentials: "omit",
        });
        if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
        if (mounted) {
          setConnected(true);
          retryCount = 0;
        }
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = "";
        while (mounted) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          const lines = buf.split("\n");
          buf = lines.pop() ?? "";
          for (const line of lines) {
            const data = parseSSELine(line.trim());
            if (data) onData(data);
          }
        }
        if (mounted) {
          setConnected(false);
          retryId = setTimeout(connectFetch, 3000);
        }
      } catch (e) {
        if (mounted && (e as Error).name !== "AbortError") {
          setConnected(false);
          urlIndex++;
          retryCount++;
          const backoff = Math.min(1500 + retryCount * 800, 6000);
          retryId = setTimeout(connectFetch, backoff);
        }
      }
    };

    // L3 启动含网关审批，需 8 秒以上，初始延迟 8 秒
    const INITIAL_DELAY_MS = 8000;
    const startId = setTimeout(connectFetch, INITIAL_DELAY_MS);

    return () => {
      mounted = false;
      clearTimeout(startId);
      if (retryId) clearTimeout(retryId);
      abortRef.current?.abort();
      setConnected(false);
    };
  }, [retryKey]);

  return (
    <div className="flex flex-col rounded-lg border border-cyan-500/30 bg-black/50 overflow-hidden">
      <button
        type="button"
        onClick={() => setCollapsed((c) => !c)}
        className="flex items-center justify-between px-3 py-2 bg-cyan-950/40 hover:bg-cyan-900/30 text-cyan-300 text-xs font-mono uppercase tracking-wider"
      >
        <span className="flex items-center gap-2">
          <Terminal className="w-3.5 h-3.5" />
          L3 全息监控
          {connected && (
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" title="已连接" />
          )}
        </span>
        {collapsed ? (
          <ChevronDown className="w-4 h-4" />
        ) : (
          <ChevronUp className="w-4 h-4" />
        )}
      </button>
      {!collapsed && (
        <div
          ref={scrollRef}
          className="h-32 overflow-y-auto overflow-x-hidden p-2 font-mono text-xs custom-scrollbar"
          style={{ scrollBehavior: "smooth" }}
        >
          {logs.length === 0 && (
            <div className="flex items-center justify-between gap-2 text-slate-500 py-1">
              <span>
                {connected
                  ? "等待 L3 日志流..."
                  : "连接中…（L3 启动后自动重试，若持续不可用请检查 L3 是否已启动或运行 scripts/run_l3.ps1）"}
              </span>
              {!connected && (
                <button
                  type="button"
                  onClick={() => setRetryKey((k) => k + 1)}
                  className="flex items-center gap-1 px-2 py-0.5 rounded text-cyan-400 hover:bg-cyan-900/30 text-xs"
                  title="手动重试连接"
                >
                  <RefreshCw className="w-3 h-3" />
                  重试
                </button>
              )}
            </div>
          )}
          {logs.map((entry, i) => (
            <div
              key={`${entry.ts}-${i}`}
              className={`py-0.5 break-words ${levelColor(entry.level)}`}
            >
              {entry.message}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
