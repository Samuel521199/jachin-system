/**
 * BI 分析 — 每日战报（scripts/run_bi_daily_report.py）
 * 手动启动走 L3 SSE；定时配置写入 ~/.jachin/data/bi_console_scheduler_state.json
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { motion } from "framer-motion";
import {
  Activity,
  BarChart3,
  CalendarClock,
  CheckCircle2,
  Clock3,
  Loader2,
  Play,
  Radio,
  Square,
  TimerReset,
  TrendingUp,
  Zap,
} from "lucide-react";
import { cn } from "../../utils/cn";
import {
  clearL3SkillsBaseUrlCache,
  getBiDailyReportStreamUrlAsync,
  getL3MonitorApiUrlAsync,
} from "../../lib/api";

const DEFAULT_HOUR_BEIJING = 8;
const DEFAULT_MINUTE_BEIJING = 0;

const PIPELINE_STAGES = [
  { label: "采集", desc: "数据抓取" },
  { label: "归因", desc: "多维表" },
  { label: "推演", desc: "策略分析" },
  { label: "投递", desc: "战报同步" },
];

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
  const scheduledLabel = hourlyRecurring
    ? `每小时 ${String(minuteBeijing).padStart(2, "0")} 分`
    : `${String(hourBeijing).padStart(2, "0")}:${String(minuteBeijing).padStart(2, "0")}`;
  const runState = l3Probing ? "L3 探测中" : running ? "分析运行中" : doneOk === true ? "最近完成" : "待命";
  const logCount = displayLogs.length;

  return (
    <div className="relative h-full min-h-0 overflow-auto p-5 text-slate-200 sm:p-6">
      {showNotify && (
        <motion.div
          initial={{ opacity: 0, y: -10, scale: 0.98 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          className={cn(
            "fixed right-6 top-20 z-[100] max-w-sm rounded-[8px] border px-4 py-3 text-sm backdrop-blur-xl",
            "border-emerald-300/25 bg-slate-950/92 text-emerald-100 shadow-[0_0_32px_rgba(52,211,153,0.16)]"
          )}
          role="status"
        >
          BI 每日战报已完成
        </motion.div>
      )}

      <div className="mx-auto flex max-w-[1180px] flex-col gap-5">
        <motion.header
          className="jarvis-panel relative overflow-hidden rounded-[8px] border border-cyan-200/[0.08] bg-cyan-300/[0.018] p-5"
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.32, ease: [0.22, 1, 0.36, 1] }}
        >
          <div className="jarvis-hero-grid opacity-[0.2]" aria-hidden />
          <div className="relative z-10 grid gap-5 lg:grid-cols-[minmax(0,1fr)_360px]">
            <div className="flex min-w-0 items-center gap-5">
              <div className="relative flex h-24 w-24 flex-shrink-0 items-center justify-center rounded-full border border-cyan-200/10 bg-cyan-300/[0.035] shadow-[0_0_38px_rgba(34,211,238,0.11)]">
                <motion.div
                  className="absolute inset-3 rounded-full border border-cyan-200/20"
                  animate={{ rotate: 360 }}
                  transition={{ duration: 18, repeat: Infinity, ease: "linear" }}
                />
                <motion.div
                  className="absolute inset-6 rounded-full border border-emerald-200/20 border-t-emerald-300/80"
                  animate={{ rotate: -360 }}
                  transition={{ duration: 9, repeat: Infinity, ease: "linear" }}
                />
                <BarChart3 className="relative h-8 w-8 text-cyan-100" aria-hidden />
              </div>
              <div className="min-w-0">
                <p className="mb-2 inline-flex rounded-full border border-cyan-200/[0.09] bg-cyan-300/[0.035] px-3 py-1 font-mono text-[10px] uppercase tracking-[0.18em] text-cyan-100/75">
                  BI Growth Core
                </p>
                <h1 className="text-2xl font-semibold text-slate-100 sm:text-3xl">BI 分析</h1>
                <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-400">
                  每日增长战报、数据采集、策略推演与投递状态集中在这里。默认只保留关键操作，细节进入 Mind Stream。
                </p>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <StatusCard icon={Activity} label="任务状态" value={runState} active={running || l3Probing} />
              <StatusCard icon={CalendarClock} label="下一节律" value={scheduledLabel} active={schedulerActive} />
              <StatusCard icon={Radio} label="调度器" value={schedulerActive ? "Active" : "Standby"} active={schedulerActive} />
              <StatusCard icon={TrendingUp} label="日志流" value={`${logCount} 条`} active={logCount > 0} />
            </div>
          </div>
        </motion.header>

        <div className="grid min-h-0 gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
          <div className="flex min-h-0 flex-col gap-5">
            <motion.section
              className="jarvis-panel rounded-[8px] border border-cyan-200/[0.08] bg-slate-950/35 p-5"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.32, delay: 0.04 }}
            >
              <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
                <div>
                  <p className="font-mono text-[10px] uppercase tracking-[0.22em] text-cyan-200/55">
                    Mission Pipeline
                  </p>
                  <h2 className="mt-1 text-base font-semibold text-slate-100">增长战报链路</h2>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    disabled={l3OrRun}
                    onClick={() => void handleStart()}
                    className={cn(
                      "inline-flex h-10 items-center gap-2 rounded-[8px] px-4 text-sm font-semibold transition-all duration-200",
                      l3OrRun
                        ? "cursor-not-allowed border border-slate-700/80 bg-slate-900/65 text-slate-500"
                        : "border border-cyan-200/30 bg-cyan-300/18 text-cyan-50 shadow-[0_0_26px_rgba(34,211,238,0.14)] hover:-translate-y-0.5 hover:bg-cyan-300/24"
                    )}
                  >
                    {l3Probing ? (
                      <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                    ) : (
                      <Play className="h-4 w-4" aria-hidden />
                    )}
                    {l3Probing ? "探测中" : running ? "运行中" : "启动分析"}
                  </button>
                  <button
                    type="button"
                    disabled={!(running || l3Probing)}
                    onClick={() => void handleStop()}
                    className={cn(
                      "inline-flex h-10 items-center gap-2 rounded-[8px] border px-4 text-sm font-semibold transition-all duration-200",
                      running || l3Probing
                        ? "border-rose-300/35 bg-rose-500/12 text-rose-100 hover:bg-rose-500/18"
                        : "cursor-not-allowed border-slate-700/70 bg-slate-900/45 text-slate-600"
                    )}
                  >
                    <Square className="h-3.5 w-3.5 fill-current" aria-hidden />
                    停止
                  </button>
                </div>
              </div>

              <div className="grid gap-3 md:grid-cols-4">
                {PIPELINE_STAGES.map((stage, index) => {
                  const active = running && index <= 1;
                  return (
                    <motion.div
                      key={stage.label}
                      className={cn(
                        "relative overflow-hidden rounded-[8px] border p-4 transition-colors",
                        active
                          ? "border-cyan-200/25 bg-cyan-300/[0.055]"
                          : "border-cyan-200/[0.08] bg-slate-900/42"
                      )}
                      whileHover={{ y: -2 }}
                      transition={{ type: "spring", stiffness: 360, damping: 28 }}
                    >
                      <div className="mb-4 flex items-center justify-between">
                        <span className="font-mono text-[10px] text-slate-500">0{index + 1}</span>
                        <span
                          className={cn(
                            "h-2 w-2 rounded-full",
                            active ? "bg-cyan-200 shadow-[0_0_14px_rgba(103,232,249,0.85)]" : "bg-slate-600"
                          )}
                        />
                      </div>
                      <div className="text-sm font-semibold text-slate-100">{stage.label}</div>
                      <div className="mt-1 text-xs text-slate-500">{stage.desc}</div>
                      {active && <div className="absolute inset-x-0 bottom-0 h-px bg-cyan-200/55" />}
                    </motion.div>
                  );
                })}
              </div>
            </motion.section>

            <motion.section
              className="jarvis-panel flex min-h-[320px] flex-1 flex-col overflow-hidden rounded-[8px] border border-cyan-200/[0.08] bg-slate-950/38"
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.32, delay: 0.08 }}
            >
              <div className="flex flex-shrink-0 items-center justify-between border-b border-cyan-200/[0.07] px-5 py-3">
                <div>
                  <p className="font-mono text-[10px] uppercase tracking-[0.22em] text-cyan-200/55">
                    Mind Stream
                  </p>
                  <h2 className="mt-1 text-sm font-semibold text-slate-200">实时运行流</h2>
                </div>
                {doneOk !== null && (
                  <span
                    className={cn(
                      "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-medium",
                      doneOk
                        ? "border-emerald-300/20 bg-emerald-300/10 text-emerald-200"
                        : "border-rose-300/20 bg-rose-400/10 text-rose-200"
                    )}
                  >
                    {doneOk ? <CheckCircle2 className="h-3.5 w-3.5" /> : <Square className="h-3 w-3 fill-current" />}
                    {doneOk ? "Success" : "Stopped"}
                  </span>
                )}
              </div>
              <pre className="min-h-0 flex-1 overflow-y-auto px-5 py-4 font-mono text-[11px] leading-6 text-cyan-50/78">
                {displayLogs.length === 0 ? (
                  <span className="text-slate-500">等待启动或定时日志，新的战报事件会在这里流入。</span>
                ) : (
                  displayLogs.map((line, i) => (
                    <div
                      key={`${i}-${line.slice(0, 24)}`}
                      className="border-l border-cyan-200/10 pl-3 text-slate-300/88"
                    >
                      {line}
                    </div>
                  ))
                )}
                <div ref={logsEndRef} />
              </pre>
            </motion.section>
          </div>

          <motion.aside
            className="flex flex-col gap-5"
            initial={{ opacity: 0, x: 8 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ duration: 0.32, delay: 0.06 }}
          >
            <section className="jarvis-panel rounded-[8px] border border-cyan-200/[0.08] bg-slate-950/38 p-5">
              <div className="mb-5 flex items-center justify-between gap-3">
                <div>
                  <p className="font-mono text-[10px] uppercase tracking-[0.22em] text-cyan-200/55">
                    Schedule Core
                  </p>
                  <h2 className="mt-1 text-base font-semibold text-slate-100">定时节律</h2>
                </div>
                <button
                  type="button"
                  disabled={scheduleLoading}
                  onClick={() => void handleScheduleToggle(!schedulerActive)}
                  className={cn(
                    "relative h-7 w-12 rounded-full border transition-all duration-300",
                    schedulerActive
                      ? "border-emerald-300/35 bg-emerald-300/18"
                      : "border-slate-600/80 bg-slate-900/70",
                    scheduleLoading && "opacity-50"
                  )}
                  aria-label="开启到点自动跑"
                >
                  <span
                    className={cn(
                      "absolute top-1 h-5 w-5 rounded-full transition-all duration-300",
                      schedulerActive
                        ? "left-6 bg-emerald-200 shadow-[0_0_16px_rgba(110,231,183,0.55)]"
                        : "left-1 bg-slate-500"
                    )}
                  />
                </button>
              </div>

              <div className="grid grid-cols-[1fr_auto_1fr] items-end gap-3">
                <label className="space-y-1.5 text-xs text-slate-500">
                  时
                  <input
                    type="number"
                    min={0}
                    max={23}
                    value={hourBeijing}
                    disabled={l3OrRun}
                    onChange={(e) => setHourBeijing(Number(e.target.value))}
                    className="h-11 w-full rounded-[8px] border border-cyan-200/12 bg-slate-950/70 px-3 font-mono text-sm text-cyan-50 outline-none transition focus:border-cyan-200/35"
                  />
                </label>
                <span className="pb-3 font-mono text-slate-500">:</span>
                <label className="space-y-1.5 text-xs text-slate-500">
                  分
                  <input
                    type="number"
                    min={0}
                    max={59}
                    value={minuteBeijing}
                    disabled={l3OrRun}
                    onChange={(e) => setMinuteBeijing(Number(e.target.value))}
                    className="h-11 w-full rounded-[8px] border border-cyan-200/12 bg-slate-950/70 px-3 font-mono text-sm text-cyan-50 outline-none transition focus:border-cyan-200/35"
                  />
                </label>
              </div>

              <label className="mt-4 flex cursor-pointer items-center justify-between gap-3 rounded-[8px] border border-cyan-200/[0.07] bg-slate-900/36 px-3 py-3">
                <span className="flex items-center gap-2 text-sm text-slate-300">
                  <TimerReset className="h-4 w-4 text-cyan-200/80" aria-hidden />
                  每小时定点
                </span>
                <input
                  type="checkbox"
                  className="h-4 w-4 accent-cyan-300"
                  checked={hourlyRecurring}
                  disabled={l3OrRun}
                  onChange={(e) => setHourlyRecurring(e.target.checked)}
                />
              </label>

              <button
                type="button"
                disabled={l3OrRun || saveScheduleLoading}
                onClick={() => void handleSaveSchedule()}
                className={cn(
                  "mt-4 inline-flex h-10 w-full items-center justify-center gap-2 rounded-[8px] border text-sm font-semibold transition-all",
                  l3OrRun || saveScheduleLoading
                    ? "cursor-not-allowed border-slate-700/80 bg-slate-900/45 text-slate-600"
                    : "border-cyan-200/20 bg-cyan-300/[0.06] text-cyan-100 hover:bg-cyan-300/[0.1]"
                )}
              >
                {saveScheduleLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Clock3 className="h-4 w-4" />}
                {saveScheduleLoading ? "保存中" : "保存节律"}
              </button>

              {scheduleSaveBanner && (
                <p className="mt-3 rounded-[8px] border border-emerald-300/12 bg-emerald-300/[0.045] px-3 py-2 text-xs leading-5 text-emerald-200/85" role="status">
                  {scheduleSaveBanner}
                </p>
              )}
            </section>

            <section className="jarvis-panel rounded-[8px] border border-cyan-200/[0.08] bg-slate-950/38 p-5">
              <div className="mb-4 flex items-center gap-2">
                <Zap className="h-4 w-4 text-cyan-200" aria-hidden />
                <h2 className="text-base font-semibold text-slate-100">运行摘要</h2>
              </div>
              <div className="space-y-3">
                <SummaryRow label="执行入口" value="L3 SSE" />
                <SummaryRow label="战报脚本" value="run_bi_daily_report.py" />
                <SummaryRow label="定时模式" value={schedulerActive ? "已开启" : "未开启"} />
                <SummaryRow label="北京时间" value={scheduledLabel} />
              </div>
            </section>
          </motion.aside>
        </div>
      </div>
    </div>
  );
}

function StatusCard({
  icon: Icon,
  label,
  value,
  active,
}: {
  icon: typeof Activity;
  label: string;
  value: string;
  active: boolean;
}) {
  return (
    <div className="rounded-[8px] border border-cyan-200/[0.08] bg-slate-950/38 p-3">
      <div className="mb-3 flex items-center justify-between">
        <Icon className={cn("h-4 w-4", active ? "text-cyan-200" : "text-slate-500")} aria-hidden />
        <span
          className={cn(
            "h-1.5 w-1.5 rounded-full",
            active ? "bg-cyan-200 shadow-[0_0_12px_rgba(103,232,249,0.8)]" : "bg-slate-600"
          )}
        />
      </div>
      <p className="text-[11px] text-slate-500">{label}</p>
      <p className="mt-1 truncate text-sm font-semibold text-slate-100">{value}</p>
    </div>
  );
}

function SummaryRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-[8px] border border-cyan-200/[0.07] bg-slate-900/32 px-3 py-2">
      <span className="text-xs text-slate-500">{label}</span>
      <span className="max-w-[190px] truncate font-mono text-xs text-cyan-100/85">{value}</span>
    </div>
  );
}
