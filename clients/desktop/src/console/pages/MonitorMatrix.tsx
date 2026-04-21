/**
 * 巡检中枢 — Kalaroko 默认场景多轮 E2E + Qwen 综合分析（SSE / Mind Stream）
 * SSE URL 经由 getKalarokoMonitorStreamUrl：开发环境走 Vite `/l3` 代理，避免直连端口跨域。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Radar } from "lucide-react";
import { cn } from "../../utils/cn";
import { getKalarokoMonitorStreamUrl } from "../../lib/api";

export function MonitorMatrix() {
  const [runs, setRuns] = useState(4);
  const [intervalSec, setIntervalSec] = useState(30);
  const [running, setRunning] = useState(false);
  const [logs, setLogs] = useState<string[]>([]);
  const [llmSummary, setLlmSummary] = useState<string | null>(null);
  const [reportMarkdown, setReportMarkdown] = useState<string | null>(null);
  const [doneOk, setDoneOk] = useState<boolean | null>(null);
  const [showNotify, setShowNotify] = useState(false);
  const logsEndRef = useRef<HTMLDivElement>(null);
  const esRef = useRef<EventSource | null>(null);

  useEffect(
    () => () => {
      esRef.current?.close();
    },
    []
  );

  useEffect(() => {
    logsEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs, llmSummary, reportMarkdown]);

  const handleStart = useCallback(() => {
    esRef.current?.close();
    setLogs([
      "> 初始化 E2E 巡检矩阵…",
      `> 计划执行: ${runs} 轮, 间隔: ${intervalSec} 秒`,
      "> 连接 L3 SSE 流…",
    ]);
    setLlmSummary(null);
    setReportMarkdown(null);
    setDoneOk(null);
    setShowNotify(false);

    const streamUrl = getKalarokoMonitorStreamUrl({
      runs: Number.isFinite(runs) ? runs : 4,
      interval: Number.isFinite(intervalSec) ? intervalSec : 30,
    });
    const eventSource = new EventSource(streamUrl);
    esRef.current = eventSource;
    setRunning(true);

    eventSource.onmessage = (event: MessageEvent<string>) => {
      try {
        const data = JSON.parse(event.data) as Record<string, unknown>;
        if (data.type === "done") {
          const ok = data.ok === true;
          setDoneOk(ok);
          const md = data.markdown_report;
          if (typeof md === "string" && md.length > 0) {
            setReportMarkdown(md);
          } else {
            setReportMarkdown(null);
          }
          const analysis = data.llm_analysis;
          if (typeof analysis === "string" && analysis.length > 0) {
            setLlmSummary(analysis);
          } else {
            setLlmSummary(null);
          }
          setLogs((prev) => [...prev, "> █ 巡检任务全链路执行完毕。"]);
          setRunning(false);
          eventSource.close();
          if (ok) {
            setShowNotify(true);
            window.setTimeout(() => setShowNotify(false), 9000);
          }
          return;
        }
        if (data.type === "error") {
          const msg = typeof data.message === "string" ? data.message : "未知错误";
          setLogs((prev) => [...prev, `> [ERROR] ${msg}`]);
          setRunning(false);
          setDoneOk(false);
          eventSource.close();
          return;
        }
        if (typeof data.line === "string") {
          setLogs((prev) => [...prev, `> ${data.line}`]);
        }
      } catch {
        /* ignore malformed SSE chunk */
      }
    };

    eventSource.onerror = () => {
      setLogs((prev) => [...prev, "> [WARN] 巡检流意外中断（请确认 L3 已启动且 `/l3` 代理可用）。"]);
      setRunning(false);
      setDoneOk(false);
      eventSource.close();
    };
  }, [runs, intervalSec]);

  return (
    <div className="relative flex h-full min-h-0 flex-col gap-5 p-6 text-cyan-300">
      {showNotify && (
        <div
          className={cn(
            "fixed right-6 top-20 z-[100] max-w-sm rounded-lg border px-4 py-3 text-sm backdrop-blur",
            "border-cyan-500/45 bg-black/90 text-cyan-100 shadow-[0_0_28px_rgba(34,211,238,0.18)]"
          )}
          role="status"
        >
          巡检报告已生成，正准备接入 Lark 管道。
        </div>
      )}

      <header className="flex flex-shrink-0 flex-wrap items-center gap-3 border-b border-cyan-500/15 pb-4">
        <div className="flex h-10 w-10 items-center justify-center rounded-lg border border-cyan-500/25 bg-cyan-500/10">
          <Radar className="h-5 w-5 text-cyan-300" aria-hidden />
        </div>
        <div>
          <h2
            className="font-sci-fi text-lg font-semibold tracking-wide text-white"
            style={{ textShadow: "0 0 12px rgba(34, 211, 238, 0.45)" }}
          >
            ■ Kalaroko E2E 巡检雷达
          </h2>
          <p className="text-xs text-cyan-700/90">巡检中枢 · Mind Stream · Qwen 综合分析</p>
        </div>
      </header>

      {/* 控制台区：极简表单 */}
      <section className="flex flex-shrink-0 flex-wrap items-end gap-4 rounded-xl border border-cyan-500/20 bg-cyan-500/[0.05] p-4">
        <label className="flex flex-col gap-1 text-xs text-cyan-600/90">
          执行轮数 (Runs)
          <input
            type="number"
            min={1}
            max={99}
            value={runs}
            disabled={running}
            onChange={(e) => setRuns(Number(e.target.value))}
            className="w-24 rounded border border-cyan-500/35 bg-black/60 px-2 py-1.5 font-mono text-cyan-100 outline-none focus:border-cyan-400/60"
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-cyan-600/90">
          轮次间隔 (秒)
          <input
            type="number"
            min={0}
            max={3600}
            value={intervalSec}
            disabled={running}
            onChange={(e) => setIntervalSec(Number(e.target.value))}
            className="w-28 rounded border border-cyan-500/35 bg-black/60 px-2 py-1.5 font-mono text-cyan-100 outline-none focus:border-cyan-400/60"
          />
        </label>
        <button
          type="button"
          disabled={running}
          onClick={handleStart}
          className={cn(
            "ml-auto rounded-lg px-5 py-2.5 text-sm font-bold transition-all",
            running
              ? "cursor-not-allowed bg-slate-800 text-slate-500"
              : "bg-cyan-400 text-black shadow-[0_0_24px_rgba(34,211,238,0.35)] hover:bg-cyan-300"
          )}
        >
          {running ? "巡检中…" : "🚀 启动全链路巡检"}
        </button>
      </section>

      {/* Mind Stream 终端 */}
      <section className="flex min-h-0 flex-1 flex-col gap-2">
        <div className="text-[11px] font-medium uppercase tracking-[0.2em] text-cyan-600/75">
          # MIND STREAM :: E2E MONITOR
        </div>
        <div
          className={cn(
            "flex min-h-[280px] flex-1 flex-col overflow-y-auto rounded-lg border border-cyan-500/25 p-4",
            "bg-[#0a0e17] font-mono text-sm leading-relaxed text-cyan-300",
            "[text-shadow:0_0_10px_rgba(6,182,212,0.12)]"
          )}
        >
          {logs.length === 0 && !llmSummary && !reportMarkdown && (
            <div className="text-cyan-700/65">等待启动… 黑底青字日志将由此流出。</div>
          )}
          {logs.map((log, index) => (
            <div key={`${index}-${log.slice(0, 48)}`} className="mb-1 break-words whitespace-pre-wrap">
              {log}
            </div>
          ))}
          <div ref={logsEndRef} />
        </div>
      </section>

      {/* 一至七节 Markdown：四轮各轮报告以 --- 拼接 */}
      {reportMarkdown != null && reportMarkdown !== "" && (
        <section className="flex max-h-[min(56vh,520px)] min-h-[160px] flex-shrink-0 flex-col gap-2 overflow-hidden rounded-lg border border-cyan-500/25 bg-black/50 p-3">
          <div className="flex-shrink-0 text-[11px] font-medium uppercase tracking-wider text-cyan-500/80">
            巡检报告 · Markdown（一至七节 · 含多轮对比）
          </div>
          <div
            className={cn(
              "min-h-0 flex-1 overflow-y-auto text-sm leading-relaxed text-cyan-100/95",
              "[&_h1]:mb-2 [&_h1]:mt-3 [&_h1]:text-base [&_h1]:font-semibold [&_h1]:text-white",
              "[&_h2]:mb-2 [&_h2]:mt-4 [&_h2]:text-sm [&_h2]:font-semibold [&_h2]:text-cyan-200",
              "[&_h3]:mb-1 [&_h3]:mt-2 [&_h3]:text-xs [&_h3]:font-medium [&_h3]:text-cyan-300/95",
              "[&_table]:w-full [&_table]:border-collapse [&_table]:text-xs",
              "[&_th]:border [&_th]:border-cyan-600/35 [&_th]:bg-cyan-950/50 [&_th]:px-2 [&_th]:py-1 [&_th]:text-left",
              "[&_td]:border [&_td]:border-cyan-700/25 [&_td]:px-2 [&_td]:py-1",
              "[&_pre]:overflow-x-auto [&_pre]:rounded [&_pre]:border [&_pre]:border-cyan-800/40 [&_pre]:bg-black/80 [&_pre]:p-2 [&_pre]:text-[11px] [&_pre]:text-cyan-200/90",
              "[&_code]:font-mono [&_code]:text-[11px]",
              "[&_hr]:my-4 [&_hr]:border-cyan-700/30",
              "[&_p]:my-1 [&_li]:my-0.5"
            )}
          >
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={{
                a: ({ children, ...props }) => (
                  <a {...props} className="text-cyan-400 underline underline-offset-2 hover:text-cyan-300" target="_blank" rel="noreferrer">
                    {children}
                  </a>
                ),
              }}
            >
              {reportMarkdown}
            </ReactMarkdown>
          </div>
        </section>
      )}

      {/* Qwen 结论（在多轮实测报告之后） */}
      {llmSummary != null && llmSummary !== "" && (
        <section
          className="flex-shrink-0 border-l-4 border-cyan-400 bg-white/[0.04] px-4 py-3"
          style={{ marginTop: "4px" }}
        >
          <h3 className="mt-0 mb-2 text-base font-semibold text-white">🧠 Qwen-Max 综合分析结论</h3>
          <p className="m-0 whitespace-pre-wrap leading-relaxed text-slate-300">{llmSummary}</p>
        </section>
      )}
      {doneOk === false && (
        <p className="flex-shrink-0 text-sm text-rose-400/95">本次巡检未全部通过，请查看上方错误与断言信息。</p>
      )}
    </div>
  );
}
