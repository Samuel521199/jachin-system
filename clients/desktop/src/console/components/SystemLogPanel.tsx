/**
 * SystemLogPanel - L3 全息监控终端
 *
 * 订阅 GET /api/system/logs/stream SSE，将调度器、锁竞争、任务成败等日志
 * 实时展示到控制台面板，按级别着色（INFO/SUCCESS/WARNING/ERROR），自动滚动到底部。
 */

import { useEffect, useRef, useState, useCallback } from "react";
import { ChevronDown, ChevronUp, Terminal } from "lucide-react";
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

export function SystemLogPanel() {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [collapsed, setCollapsed] = useState(false);
  const [connected, setConnected] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

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
    let es: EventSource | null = null;
    let retryId: ReturnType<typeof setTimeout> | null = null;
    let urlIndex = 0;
    let retryCount = 0;
    let mounted = true;

    const onMessage = (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data) as { message?: string; level?: string; ts?: number };
        const msg = data?.message ?? "";
        const level = (data?.level ?? "INFO").toUpperCase();
        const ts = typeof data?.ts === "number" ? data.ts : Date.now() / 1000;
        if (msg) {
          setLogs((prev) => {
            const next = [...prev, { message: msg, level, ts }];
            return next.slice(-500);
          });
        }
      } catch {}
    };

    const connect = () => {
      if (!mounted) return;
      if (urlIndex >= urls.length) {
        urlIndex = 0;
        retryCount++;
        const backoff = Math.min(2000 + retryCount * 1000, 5000);
        retryId = setTimeout(connect, backoff);
        return;
      }
      const url = urls[urlIndex];
      es = new EventSource(url);
      es.onopen = () => {
        if (mounted) {
          setConnected(true);
          retryCount = 0;
        }
      };
      es.onerror = () => {
        setConnected(false);
        es?.close();
        es = null;
        urlIndex++;
        if (!mounted) return;
        retryCount++;
        const backoff = Math.min(1500 + retryCount * 800, 5000);
        retryId = setTimeout(connect, backoff);
      };
      es.onmessage = onMessage;
    };

    // L3 HTTP 服务启动需几秒（含网关审批），初始延迟 3 秒减少 ECONNREFUSED
    const INITIAL_DELAY_MS = 3000;
    const startId = setTimeout(connect, INITIAL_DELAY_MS);

    return () => {
      mounted = false;
      clearTimeout(startId);
      if (retryId) clearTimeout(retryId);
      es?.close();
      setConnected(false);
    };
  }, []);

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
            <div className="text-slate-500 py-1">
              {connected ? "等待 L3 日志流..." : "连接中…（L3 启动后自动重试）"}
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
