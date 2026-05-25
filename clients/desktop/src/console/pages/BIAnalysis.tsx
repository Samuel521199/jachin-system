/**
 * BI 分析 — 每日战报（scripts/run_bi_daily_report.py）
 * 手动启动走 L3 SSE；定时配置写入 ~/.jachin/data/bi_console_scheduler_state.json
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { BarChart3 } from "lucide-react";
import { cn } from "../../utils/cn";
import {
  clearL3SkillsBaseUrlCache,
  getBiDailyReportStreamUrlAsync,
  getL3MonitorApiUrlAsync,
} from "../../lib/api";

const DEFAULT_HOUR_BEIJING = 8;
const DEFAULT_MINUTE_BEIJING = 0;

function parseScheduleLogLine(data: Record<string, unknown>): string | null {
  if (typeof data.line === "string") return data.line;
  if (data.type === "scheduled_start") {
    const pattern = typeof data.pattern === "string" ? data.pattern : "";
    return `[定时] 开始 BI 战报${pattern ? ` · ${pattern}` : ""}`;
  }
  if (data.type === "scheduled_done") {
    const ok = data.ok === true;
    const err = typeof data.error === "string" && data.error ? ` · ${data.error}` : "";
    return `[定时] ${ok ? "完成" : "失败"}${err}`;
  }
  if (data.type === "error" && typeof data.message === "string") {
    return `[定时 ERROR] ${data.message}`;
  }
  return null;
}

export function BIAnalysis() {
  const [hourBeijing, setHourBeijing] = useState(DEFAULT_HOUR_BEIJING);
  const [minuteBeijing, setMinuteBeijing] = useState(DEFAULT_MINUTE_BEIJING);
  const [hourlyRecurring, setHourlyRecurring] = useState(false);
  const [running, setRunning] = useState(false);
  const [l3Probing, setL3Probing] = useState(false);
  const [manualLogs, setManualLogs] = useState<string[]>([]);
  const [scheduleLogLines, setScheduleLogLines] = useState<string[]>([]);
  const displayLogs = useMemo(
    () => [...scheduleLogLines, ...manualLogs],
    [scheduleLogLines, manualLogs]
  );
  const [doneOk, setDoneOk] = useState<boolean | null>(null);
  const [showNotify, setShowNotify] = useState(false);
  const [schedulerActive, setSchedulerActive] = useState(false);
  const [scheduleLoading, setScheduleLoading] = useState(false);
  const [saveScheduleLoading, setSaveScheduleLoading] = useState(false);
  const [scheduleSaveBanner, setScheduleSaveBanner] = useState<string | null>(null);
  const scheduleSaveTimerRef = useRef<ReturnType<typeof window.setTimeout> | null>(null);
  const logsEndRef = useRef<HTMLDivElement>(null);
  const esRef = useRef<EventSource | null>(null);
  const scheduleEsRef = useRef<EventSource | null>(null);

  useEffect(
    () => () => {
      esRef.current?.close();
      scheduleEsRef.current?.close();
      if (scheduleSaveTimerRef.current) window.clearTimeout(scheduleSaveTimerRef.current);
    },
    []
  );

  const refreshScheduleStatus = useCallback(async () => {
    const apply = async (bypass: boolean) => {
      const url = await getL3MonitorApiUrlAsync("/api/v1/bi-daily-report/schedule/status", {
        bypassCache: bypass,
      });
      const res = await fetch(url);
      const data = (await res.json()) as {
        active?: boolean;
        hour_beijing?: number;
        minute_beijing?: number;
        hourly_recurring?: boolean;
      };
      if (typeof data.active === "boolean") setSchedulerActive(data.active);
      if (typeof data.hour_beijing === "number") setHourBeijing(data.hour_beijing);
      if (typeof data.minute_beijing === "number") setMinuteBeijing(data.minute_beijing);
      if (typeof data.hourly_recurring === "boolean") setHourlyRecurring(data.hourly_recurring);
    };
    try {
      await apply(false);
    } catch {
      try {
        clearL3SkillsBaseUrlCache();
        await apply(true);
      } catch {
        setSchedulerActive(false);
      }
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

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      let streamUrl: string;
      try {
        streamUrl = await getL3MonitorApiUrlAsync("/api/v1/bi-daily-report/schedule/log-stream");
      } catch {
        return;
      }
      if (cancelled) return;
      const es = new EventSource(streamUrl);
      scheduleEsRef.current = es;
      es.onmessage = (event: MessageEvent<string>) => {
        try {
          const data = JSON.parse(event.data) as Record<string, unknown>;
          const line = parseScheduleLogLine(data);
          if (line) setScheduleLogLines((prev) => [...prev, `> ${line}`]);
        } catch {
          /* ignore */
        }
      };
    })();
    return () => {
      cancelled = true;
      scheduleEsRef.current?.close();
    };
  }, []);

  const handleSaveSchedule = useCallback(async () => {
    setSaveScheduleLoading(true);
    setScheduleSaveBanner(null);
    try {
      const url = await getL3MonitorApiUrlAsync("/api/v1/bi-daily-report/schedule/toggle", {
        bypassCache: true,
      });
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          enabled: schedulerActive,
          hour_beijing: hourBeijing,
          minute_beijing: minuteBeijing,
          hourly_recurring: hourlyRecurring,
        }),
      });
      const data = (await res.json().catch(() => ({}))) as { active?: boolean; message?: string };
      if (typeof data.active === "boolean") setSchedulerActive(data.active);
      setScheduleSaveBanner(
        typeof data.message === "string" && data.message
          ? data.message
          : "定时配置已保存（若开关为开，将按新时刻重排任务）"
      );
      if (scheduleSaveTimerRef.current) window.clearTimeout(scheduleSaveTimerRef.current);
      scheduleSaveTimerRef.current = window.setTimeout(() => setScheduleSaveBanner(null), 8000);
    } catch {
      setScheduleSaveBanner("保存失败：无法连接 L3，请确认侧车已启动。");
    } finally {
      setSaveScheduleLoading(false);
    }
  }, [hourBeijing, hourlyRecurring, minuteBeijing, schedulerActive]);

  const handleScheduleToggle = useCallback(async (enabled: boolean) => {
    setScheduleLoading(true);
    try {
      const url = await getL3MonitorApiUrlAsync("/api/v1/bi-daily-report/schedule/toggle");
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          enabled,
          hour_beijing: hourBeijing,
          minute_beijing: minuteBeijing,
          hourly_recurring: hourlyRecurring,
        }),
      });
      const data = (await res.json().catch(() => ({}))) as { active?: boolean };
      if (typeof data.active === "boolean") setSchedulerActive(data.active);
      else setSchedulerActive(enabled);
    } catch {
      setManualLogs((prev) => [...prev, "> [WARN] 定时开关请求失败。"]);
    } finally {
      setScheduleLoading(false);
    }
  }, [hourBeijing, hourlyRecurring, minuteBeijing]);

  const handleStop = useCallback(async () => {
    try {
      const url = await getL3MonitorApiUrlAsync("/api/v1/bi-daily-report/stop", { bypassCache: true });
      const res = await fetch(url, { method: "POST" });
      setManualLogs((prev) => [
        ...prev,
        res.ok ? "> 已发送停止信号…" : "> 停止请求失败（HTTP）。",
      ]);
    } catch {
      setManualLogs((prev) => [...prev, "> 停止请求失败（网络）。"]);
    }
  }, []);

  const connectSse = useCallback((streamUrl: string, introLines: string[]) => {
    setRunning(true);
    setManualLogs(introLines);
    const eventSource = new EventSource(streamUrl);
    esRef.current = eventSource;

    eventSource.onmessage = (event: MessageEvent<string>) => {
      try {
        const data = JSON.parse(event.data) as Record<string, unknown>;
        if (data.type === "done") {
          const ok = data.ok === true;
          const cancelled = data.cancelled === true;
          setDoneOk(ok);
          if (cancelled) {
            setManualLogs((prev) => [...prev, "> █ 已按停止请求结束。"]);
          } else {
            setManualLogs((prev) => [
              ...prev,
              ok ? "> █ BI 每日战报执行完毕。" : "> █ BI 战报结束（非零退出码或失败）。",
            ]);
          }
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
          setManualLogs((prev) => [...prev, `> [ERROR] ${msg}`]);
          setRunning(false);
          setDoneOk(false);
          eventSource.close();
          return;
        }
        if (typeof data.line === "string") {
          setManualLogs((prev) => [...prev, `> ${data.line}`]);
        }
      } catch {
        /* ignore */
      }
    };

    eventSource.onerror = () => {
      setManualLogs((prev) => [...prev, "> [WARN] SSE 流意外中断（请确认 L3 已启动）。"]);
      setRunning(false);
      setDoneOk(false);
      eventSource.close();
    };
  }, []);

  const handleStart = useCallback(() => {
    void (async () => {
      esRef.current?.close();
      setL3Probing(true);
      setDoneOk(null);
      setManualLogs([
        "> 正在探测本机 L3 HTTP…",
        "> 将执行: python scripts/run_bi_daily_report.py",
      ]);
      let streamUrl: string;
      try {
        streamUrl = await getBiDailyReportStreamUrlAsync();
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        setManualLogs([
          `> [ERROR] 连不上 L3：${msg}`,
          "> 请确认 L3 侧车已运行（run_l3.bat 或主程序随附 l3_node）。",
        ]);
        return;
      } finally {
        setL3Probing(false);
      }
      connectSse(streamUrl, [
        "> 初始化 BI 每日战报…",
        "> 等价命令: python scripts/run_bi_daily_report.py",
        "> 连接 L3 SSE 流…",
      ]);
    })();
  }, [connectSse]);

  useEffect(() => {
    logsEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [displayLogs]);

  const l3OrRun = l3Probing || running;

  return (
    <div className="relative flex h-full min-h-0 flex-col gap-5 p-6 text-amber-200/90">
      {showNotify && (
        <div
          className={cn(
            "fixed right-6 top-20 z-[100] max-w-sm rounded-lg border px-4 py-3 text-sm backdrop-blur",
            "border-amber-500/45 bg-black/90 text-amber-100 shadow-[0_0_28px_rgba(245,158,11,0.18)]"
          )}
          role="status"
        >
          BI 每日战报已完成。
        </div>
      )}

      <header className="flex flex-shrink-0 flex-wrap items-center gap-3 border-b border-amber-500/15 pb-4">
        <div className="flex h-10 w-10 items-center justify-center rounded-lg border border-amber-500/25 bg-amber-500/10">
          <BarChart3 className="h-5 w-5 text-amber-300" aria-hidden />
        </div>
        <div>
          <h2
            className="font-sci-fi text-lg font-semibold tracking-wide text-white"
            style={{ textShadow: "0 0 12px rgba(245, 158, 11, 0.45)" }}
          >
            ■ BI 分析
          </h2>
          <p className="text-xs text-amber-700/90">
            每日战报 ·{" "}
            <span className="font-mono text-amber-600/90">scripts/run_bi_daily_report.py</span>
          </p>
        </div>
      </header>

      <section className="flex flex-shrink-0 flex-col gap-4 rounded-xl border border-amber-500/20 bg-amber-500/[0.05] p-4">
        <div className="flex flex-wrap items-center justify-end gap-2">
          <button
            type="button"
            disabled={l3OrRun}
            onClick={() => void handleStart()}
            className={cn(
              "rounded-lg px-5 py-2.5 text-sm font-bold transition-all",
              l3OrRun
                ? "cursor-not-allowed bg-slate-800 text-slate-500"
                : "bg-amber-400 text-black shadow-[0_0_24px_rgba(245,158,11,0.35)] hover:bg-amber-300"
            )}
          >
            {l3Probing ? "探测 L3 中…" : running ? "执行中…" : "🚀 启动 BI 分析"}
          </button>
          <button
            type="button"
            disabled={!(running || l3Probing)}
            onClick={() => void handleStop()}
            className={cn(
              "rounded-lg border px-4 py-2.5 text-sm font-semibold transition-all",
              running || l3Probing
                ? "border-rose-500/50 bg-rose-950/40 text-rose-200 hover:bg-rose-900/50"
                : "cursor-not-allowed border-slate-700 bg-slate-900/40 text-slate-600"
            )}
          >
            🛑 停止
          </button>
        </div>

        <p className="text-[11px] leading-relaxed text-amber-600/75">
          「启动 BI 分析」经 L3 子进程跑完整战报流程（抓取、多维表、战略分析、推送等），日志见下方 MIND
          STREAM。Windows 下<strong className="text-amber-500/88">定时到点</strong>会另开控制台窗口（与手动 SSE
          不同，便于长时间任务观察）。YAML 侧旧调度（bi_daily_report.yaml）与本页控制台定时独立，勿重复开启两套。
        </p>

        <div className="flex flex-col gap-3 border-t border-amber-500/15 pt-4">
          <div className="text-xs font-medium text-amber-500/90">⏲️ 定时 BI 战报（北京时间）</div>
          <div className="flex flex-wrap items-end gap-3">
            <label className="flex flex-col gap-1 text-xs text-amber-600/90">
              时
              <input
                type="number"
                min={0}
                max={23}
                value={hourBeijing}
                disabled={l3OrRun}
                onChange={(e) => setHourBeijing(Number(e.target.value))}
                className="w-16 rounded border border-amber-500/35 bg-black/60 px-2 py-1.5 font-mono text-amber-100 outline-none focus:border-amber-400/60"
              />
            </label>
            <span className="mb-2 text-amber-500/50">:</span>
            <label className="flex flex-col gap-1 text-xs text-amber-600/90">
              分
              <input
                type="number"
                min={0}
                max={59}
                value={minuteBeijing}
                disabled={l3OrRun}
                onChange={(e) => setMinuteBeijing(Number(e.target.value))}
                className="w-16 rounded border border-amber-500/35 bg-black/60 px-2 py-1.5 font-mono text-amber-100 outline-none focus:border-amber-400/60"
              />
            </label>
            <button
              type="button"
              disabled={l3OrRun || saveScheduleLoading}
              onClick={() => void handleSaveSchedule()}
              className={cn(
                "mb-0.5 rounded-lg border px-4 py-2 text-xs font-semibold transition",
                l3OrRun || saveScheduleLoading
                  ? "cursor-not-allowed border-slate-700 text-slate-500"
                  : "border-amber-500/50 bg-amber-950/50 text-amber-100 hover:border-amber-400/50"
              )}
            >
              {saveScheduleLoading ? "保存中…" : "保存定时配置"}
            </button>
          </div>
          <label className="flex max-w-xl cursor-pointer items-start gap-2 text-xs text-amber-600/90">
            <input
              type="checkbox"
              className="mt-0.5 h-3.5 w-3.5 shrink-0"
              checked={hourlyRecurring}
              disabled={l3OrRun}
              onChange={(e) => setHourlyRecurring(e.target.checked)}
            />
            <span>
              <span className="font-medium text-amber-500/90">每小时定点</span>
              <span className="text-amber-600/75">
                （按「分」每个整点触发；关闭则仅在设定时刻每日一次）
              </span>
            </span>
          </label>
          {scheduleSaveBanner && (
            <p className="text-xs leading-relaxed text-emerald-400/90" role="status">
              {scheduleSaveBanner}
            </p>
          )}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <span
                className={cn(
                  "inline-block h-2.5 w-2.5 shrink-0 rounded-full",
                  schedulerActive ? "bg-emerald-400 shadow-[0_0_10px_rgba(52,211,153,0.7)]" : "bg-slate-600"
                )}
                aria-hidden
              />
              <span className="text-xs text-amber-600/85">
                {schedulerActive ? "Active" : "Inactive"} ·{" "}
                {hourlyRecurring
                  ? `每小时 · 北京 *:${String(minuteBeijing).padStart(2, "0")}`
                  : `每日 · 北京 ${String(hourBeijing).padStart(2, "0")}:${String(minuteBeijing).padStart(2, "0")}`}
              </span>
            </div>
            <label className="flex cursor-pointer items-center gap-2 text-xs text-amber-600/90">
              <span className="select-none">开启到点自动跑</span>
              <input
                type="checkbox"
                role="switch"
                className="h-4 w-9 cursor-pointer appearance-none rounded-full border border-amber-500/40 bg-black/70 transition checked:bg-emerald-600/80 disabled:opacity-40"
                checked={schedulerActive}
                disabled={scheduleLoading}
                onChange={(e) => void handleScheduleToggle(e.target.checked)}
              />
            </label>
          </div>
        </div>
      </section>

      <section className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-amber-500/15 bg-black/40">
        <div className="flex flex-shrink-0 items-center justify-between border-b border-amber-500/10 px-4 py-2">
          <span className="font-mono text-xs tracking-widest text-amber-500/80">MIND STREAM</span>
          {doneOk !== null && (
            <span
              className={cn(
                "text-xs font-medium",
                doneOk ? "text-emerald-400" : "text-rose-400"
              )}
            >
              {doneOk ? "SUCCESS" : "FAILED / STOPPED"}
            </span>
          )}
        </div>
        <pre className="min-h-0 flex-1 overflow-y-auto p-4 font-mono text-[11px] leading-relaxed text-amber-100/85">
          {displayLogs.length === 0 ? (
            <span className="text-amber-700/70">等待启动或定时日志…</span>
          ) : (
            displayLogs.map((line, i) => (
              <div key={`${i}-${line.slice(0, 24)}`}>{line}</div>
            ))
          )}
          <div ref={logsEndRef} />
        </pre>
      </section>
    </div>
  );
}
