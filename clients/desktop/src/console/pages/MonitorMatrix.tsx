/**
 * 巡检中枢 — Kalaroko 默认场景多轮 E2E + AI 综合分析（SSE / Mind Stream）
 * SSE URL 经由 getKalarokoMonitorStreamUrl：开发环境走 Vite `/l3` 代理，避免直连端口跨域。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Radar } from "lucide-react";
import { cn } from "../../utils/cn";
import { getKalarokoMonitorStreamUrlAsync, getL3MonitorApiUrlAsync } from "../../lib/api";

export function MonitorMatrix() {
  const [runs, setRuns] = useState(4);
  const [intervalSec, setIntervalSec] = useState(30);
  const [running, setRunning] = useState(false);
  const [logs, setLogs] = useState<string[]>([]);
  const [llmSummary, setLlmSummary] = useState<string | null>(null);
  const [reportMarkdown, setReportMarkdown] = useState<string | null>(null);
  const [doneOk, setDoneOk] = useState<boolean | null>(null);
  const [showNotify, setShowNotify] = useState(false);
  const [schedulerActive, setSchedulerActive] = useState(false);
  const [scheduleLoading, setScheduleLoading] = useState(false);
  const logsEndRef = useRef<HTMLDivElement>(null);
  const esRef = useRef<EventSource | null>(null);
  const sseTransientWarnAtRef = useRef(0);

  useEffect(
    () => () => {
      esRef.current?.close();
    },
    []
  );

  const refreshScheduleStatus = useCallback(async () => {
    try {
      const url = await getL3MonitorApiUrlAsync("/api/v1/monitor/schedule/status");
      const res = await fetch(url);
      const data = (await res.json()) as { ok?: boolean; active?: boolean };
      if (typeof data.active === "boolean") {
        setSchedulerActive(data.active);
      }
    } catch {
      setSchedulerActive(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    void refreshScheduleStatus();
    const id = window.setInterval(() => {
      if (!cancelled) void refreshScheduleStatus();
    }, 15000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [refreshScheduleStatus]);

  const handleStopInspection = useCallback(async () => {
    try {
      const url = await getL3MonitorApiUrlAsync("/api/v1/monitor/stop");
      const res = await fetch(url, { method: "POST" });
      const ok = res.ok;
      setLogs((prev) => [...prev, ok ? "> 已发送停止信号（下一检查点生效）…" : "> 停止请求失败（HTTP）。"]);
      if (ok) {
        esRef.current?.close();
        esRef.current = null;
        setRunning(false);
      }
    } catch {
      setLogs((prev) => [...prev, "> 停止请求失败（网络）。"]);
    }
  }, []);

  const handleScheduleToggle = useCallback(
    async (enabled: boolean) => {
      setScheduleLoading(true);
      try {
        const url = await getL3MonitorApiUrlAsync("/api/v1/monitor/schedule/toggle");
        const res = await fetch(url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled }),
        });
        const data = (await res.json().catch(() => ({}))) as {
          enabled?: boolean;
          active?: boolean;
        };
        const on = typeof data.enabled === "boolean" ? data.enabled : typeof data.active === "boolean" ? data.active : enabled;
        setSchedulerActive(on);
      } catch {
        setLogs((prev) => [...prev, "> [WARN] 定时守护开关请求失败。"]);
      } finally {
        setScheduleLoading(false);
      }
    },
    []
  );

  useEffect(() => {
    logsEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs, llmSummary, reportMarkdown]);

  const handleStart = useCallback(() => {
    void (async () => {
      esRef.current?.close();
      setLogs([
        "> 初始化 E2E 巡检矩阵…",
        `> 计划执行: ${runs} 轮, 间隔: ${intervalSec} 秒`,
        "> 正在探测 L3 并连接 SSE 流…",
      ]);
      setLlmSummary(null);
      setReportMarkdown(null);
      setDoneOk(null);
      setShowNotify(false);

      let streamUrl: string;
      try {
        streamUrl = await getKalarokoMonitorStreamUrlAsync({
          runs: Number.isFinite(runs) ? runs : 4,
          interval: Number.isFinite(intervalSec) ? intervalSec : 30,
        });
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        setLogs((prev) => [
          ...prev,
          `> [ERROR] 连不上 L3：${msg}`,
          "> 请确认 L3 已运行（同目录 run_l3.bat 或主程序随附 l3 侧车），并查看 l3_debug.log。",
        ]);
        return;
      }
      setLogs((prev) => [...prev, "> 连接 L3 SSE 流…"]);
      const eventSource = new EventSource(streamUrl);
      esRef.current = eventSource;
      setRunning(true);

    eventSource.onmessage = (event: MessageEvent<string>) => {
      try {
        const data = JSON.parse(event.data) as Record<string, unknown>;
        if (data.type === "done") {
          const ok = data.ok === true;
          const cancelled = data.cancelled === true;
          setDoneOk(ok);
          if (cancelled) {
            setLogs((prev) => [...prev, "> █ 巡检已由用户停止（部分轮次结果可能已生成）。"]);
          }
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
      if (eventSource.readyState === EventSource.CLOSED) {
        const hint = import.meta.env.DEV
          ? "请确认 L3 与 /l3 开发代理可用。"
          : "请确认本机 L3 已运行；若仅 SSE 已断、巡检仍在，请点「停止」。";
        setLogs((prev) => [...prev, `> [WARN] SSE 已结束（无自动重连）。${hint}`]);
        return;
      }
      const now = Date.now();
      if (now - sseTransientWarnAtRef.current > 8000) {
        sseTransientWarnAtRef.current = now;
        setLogs((prev) => [
          ...prev,
          "> [INFO] SSE 连接异常或抖动（可能自动重试）；未收到结束信令前仍可点「停止」",
        ]);
      }
    };
    })();
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
          <p className="text-xs text-cyan-700/90">巡检中枢 · Mind Stream · AI 综合分析</p>
        </div>
      </header>

      {/* 控制台区：极简表单 */}
      <section className="flex flex-shrink-0 flex-col gap-4 rounded-xl border border-cyan-500/20 bg-cyan-500/[0.05] p-4">
        <div className="flex flex-wrap items-end gap-4">
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
          <div className="ml-auto flex flex-wrap items-center gap-2">
            <button
              type="button"
              disabled={running}
              onClick={handleStart}
              className={cn(
                "rounded-lg px-5 py-2.5 text-sm font-bold transition-all",
                running
                  ? "cursor-not-allowed bg-slate-800 text-slate-500"
                  : "bg-cyan-400 text-black shadow-[0_0_24px_rgba(34,211,238,0.35)] hover:bg-cyan-300"
              )}
            >
              {running ? "巡检中…" : "🚀 启动全链路巡检"}
            </button>
            <button
              type="button"
              disabled={!running}
              onClick={() => void handleStopInspection()}
              className={cn(
                "rounded-lg border px-4 py-2.5 text-sm font-semibold transition-all",
                running
                  ? "border-rose-500/50 bg-rose-950/40 text-rose-200 hover:bg-rose-900/50"
                  : "cursor-not-allowed border-slate-700 bg-slate-900/40 text-slate-600"
              )}
            >
              🛑 停止巡检
            </button>
          </div>
        </div>
        <p className="text-[11px] leading-relaxed text-cyan-600/75">
          「启动全链路巡检」为<strong className="text-cyan-500/90">手动单次</strong>：按上方轮数跑完即结束（含报告 / 飞书）。
          需要<strong className="text-cyan-500/90">长期定时</strong>请打开下方「定时守护」— 首跑在开启后很快开始，之后
          <strong className="text-cyan-500/90">每小时</strong>一批（默认 4 轮×30s）；日志里可搜
          <code className="text-cyan-500/80">[kalaroko_scheduler]</code>，L3 进程需保持运行。
        </p>
        <div className="flex flex-wrap items-center gap-4 border-t border-cyan-500/15 pt-4">
          <div className="text-xs font-medium text-cyan-500/90">⏲️ 定时守护进程</div>
          <div
            className="flex items-center gap-2"
            title="每小时自动巡检（4 轮）与每日北京时间 08:00 晨报（Cron 使用 UTC 00:00，L3 内 APScheduler）"
          >
            <span
              className={cn(
                "inline-block h-2.5 w-2.5 shrink-0 rounded-full",
                schedulerActive === true ? "bg-emerald-400 shadow-[0_0_10px_rgba(52,211,153,0.7)]" : "bg-slate-600"
              )}
              aria-hidden
            />
            <span className="text-xs text-cyan-600/85">{schedulerActive ? "Active" : "Inactive"}</span>
          </div>
          <label className="ml-auto flex cursor-pointer items-center gap-2 text-xs text-cyan-600/90">
            <span className="select-none">每小时巡检 &amp; 每日 8:00 晨报</span>
            <input
              type="checkbox"
              role="switch"
              className="h-4 w-9 cursor-pointer appearance-none rounded-full border border-cyan-500/40 bg-black/70 transition checked:bg-emerald-600/80 disabled:opacity-40"
              checked={schedulerActive}
              disabled={scheduleLoading}
              onChange={(e) => void handleScheduleToggle(e.target.checked)}
            />
          </label>
        </div>
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

      {/* AI 综合分析结论（在多轮实测报告之后） */}
      {llmSummary != null && llmSummary !== "" && (
        <section
          className="flex-shrink-0 border-l-4 border-cyan-400 bg-white/[0.04] px-4 py-3"
          style={{ marginTop: "4px" }}
        >
          <h3 className="mt-0 mb-2 text-base font-semibold text-white">🧠 AI 综合分析结论</h3>
          <p className="m-0 whitespace-pre-wrap leading-relaxed text-slate-300">{llmSummary}</p>
        </section>
      )}
      {doneOk === false && (
        <p className="flex-shrink-0 text-sm text-rose-400/95">本次巡检未全部通过，请查看上方错误与断言信息。</p>
      )}
    </div>
  );
}
