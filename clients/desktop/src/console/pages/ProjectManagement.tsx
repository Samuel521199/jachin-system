/**
 * 项目管理 — PMO Copilot 触发器 + 本地定时任务调度
 * 触发按钮：打包环境走 L3 侧车 --run-pmo-copilot；开发机可回退 python scripts/run_pmo_copilot_skill.py
 * 定时任务：localStorage 持久化，支持不重复 / 每天 / 每周指定星期
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Briefcase,
  Plus,
  Trash2,
  Play,
  Save,
  Clock,
  ToggleLeft,
  ToggleRight,
  CheckCircle2,
  XCircle,
  Loader2,
  Terminal,
  CalendarClock,
  ChevronDown,
  Settings,
  FolderOpen,
  Square,
  Database,
} from "lucide-react";
import { invoke } from "@tauri-apps/api/core";
import { cn } from "../../utils/cn";

// ─── Types ───────────────────────────────────────────────────────────────────

type RepeatMode =
  | "once"
  | "daily"
  | "weekly_0"
  | "weekly_1"
  | "weekly_2"
  | "weekly_3"
  | "weekly_4"
  | "weekly_5"
  | "weekly_6";

interface ScheduledTask {
  id: string;
  name: string;
  hour: number;
  minute: number;
  repeat: RepeatMode;
  enabled: boolean;
  lastRunDate: string | null; // "YYYY-MM-DD" for daily/once; "YYYY-WW-D" for weekly
}

interface PmoSkillConfigInfo {
  path: string;
  yaml: string;
  exists: boolean;
  seeded: boolean;
}

type PmoRunPhase = "idle" | "running" | "finished" | "failed" | "stopped";

interface PmoCopilotRunStatus {
  phase: PmoRunPhase;
  pid?: number | null;
  started_at_ms?: number | null;
  finished_at_ms?: number | null;
  exit_code?: number | null;
  label?: string | null;
}

// ─── Constants ───────────────────────────────────────────────────────────────

const STORAGE_KEY = "jachin_pmo_scheduled_tasks";

const REPEAT_OPTIONS: { value: RepeatMode; label: string }[] = [
  { value: "once", label: "不重复" },
  { value: "daily", label: "每天" },
  { value: "weekly_1", label: "每周一" },
  { value: "weekly_2", label: "每周二" },
  { value: "weekly_3", label: "每周三" },
  { value: "weekly_4", label: "每周四" },
  { value: "weekly_5", label: "每周五" },
  { value: "weekly_6", label: "每周六" },
  { value: "weekly_0", label: "每周日" },
];

// ─── Helpers ─────────────────────────────────────────────────────────────────

function genId(): string {
  return `pmo_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`;
}

function todayStr(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function formatClockFromMs(ms: number): string {
  const d = new Date(ms);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function formatElapsedMs(ms: number): string {
  const totalSec = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  if (h > 0) return `${h} 小时 ${m} 分`;
  if (m > 0) return `${m} 分 ${s} 秒`;
  return `${s} 秒`;
}

/** "YYYY-WW-D" keyed by ISO week + weekday */
function thisWeekDayStr(weekday: number): string {
  const d = new Date();
  const day = d.getDay(); // 0=Sun
  const monday = new Date(d);
  monday.setDate(d.getDate() - ((day + 6) % 7));
  const week = String(
    Math.ceil(
      ((monday.getTime() - new Date(monday.getFullYear(), 0, 1).getTime()) / 86400000 + 1) / 7
    )
  ).padStart(2, "0");
  return `${monday.getFullYear()}-W${week}-${weekday}`;
}

function loadTasks(): ScheduledTask[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    return JSON.parse(raw) as ScheduledTask[];
  } catch {
    return [];
  }
}

function saveTasks(tasks: ScheduledTask[]): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(tasks));
}

function shouldFire(task: ScheduledTask, now: Date): boolean {
  if (!task.enabled) return false;
  if (now.getHours() !== task.hour || now.getMinutes() !== task.minute) return false;

  if (task.repeat === "once") {
    return task.lastRunDate === null;
  }
  if (task.repeat === "daily") {
    const today = todayStr();
    return task.lastRunDate !== today;
  }
  if (task.repeat.startsWith("weekly_")) {
    const wd = parseInt(task.repeat.slice(7), 10); // 0-6
    if (now.getDay() !== wd) return false;
    const key = thisWeekDayStr(wd);
    return task.lastRunDate !== key;
  }
  return false;
}

function markRan(task: ScheduledTask, now: Date): ScheduledTask {
  if (task.repeat === "once") {
    return { ...task, enabled: false, lastRunDate: todayStr() };
  }
  if (task.repeat === "daily") {
    return { ...task, lastRunDate: todayStr() };
  }
  if (task.repeat.startsWith("weekly_")) {
    const wd = parseInt(task.repeat.slice(7), 10);
    return { ...task, lastRunDate: thisWeekDayStr(wd) };
  }
  return { ...task, lastRunDate: todayStr() };
}

// ─── Sub-component: Task Row ─────────────────────────────────────────────────

function TaskRow({
  task,
  onToggle,
  onDelete,
  onEdit,
}: {
  task: ScheduledTask;
  onToggle: (id: string) => void;
  onDelete: (id: string) => void;
  onEdit: (id: string) => void;
}) {
  const repeatLabel =
    REPEAT_OPTIONS.find((o) => o.value === task.repeat)?.label ?? task.repeat;

  return (
    <div
      className={cn(
        "group flex items-center gap-3 rounded-lg border px-4 py-3 transition-all duration-200",
        task.enabled
          ? "border-cyan-500/25 bg-cyan-500/5 hover:border-cyan-500/40 hover:bg-cyan-500/10"
          : "border-slate-700/40 bg-slate-800/30 opacity-60 hover:opacity-80"
      )}
    >
      {/* Toggle */}
      <button
        onClick={() => onToggle(task.id)}
        className="flex-shrink-0 transition-colors"
        title={task.enabled ? "点击禁用" : "点击启用"}
      >
        {task.enabled ? (
          <ToggleRight className="h-5 w-5 text-cyan-400" />
        ) : (
          <ToggleLeft className="h-5 w-5 text-slate-500" />
        )}
      </button>

      {/* Info */}
      <div
        className="min-w-0 flex-1 cursor-pointer"
        onClick={() => onEdit(task.id)}
        title="点击编辑"
      >
        <p className="truncate text-sm font-medium text-cyan-100">{task.name || "（未命名任务）"}</p>
        <p className="mt-0.5 flex items-center gap-2 text-xs text-cyan-600">
          <Clock className="h-3 w-3 flex-shrink-0" />
          <span>
            {String(task.hour).padStart(2, "0")}:{String(task.minute).padStart(2, "0")}
          </span>
          <span className="text-slate-500">·</span>
          <span>{repeatLabel}</span>
          {task.lastRunDate && (
            <>
              <span className="text-slate-500">·</span>
              <span className="text-slate-500">上次: {task.lastRunDate.slice(0, 10)}</span>
            </>
          )}
        </p>
      </div>

      {/* Delete */}
      <button
        onClick={() => onDelete(task.id)}
        className="flex-shrink-0 opacity-0 transition-opacity group-hover:opacity-100 text-rose-500 hover:text-rose-400"
        title="删除任务"
      >
        <Trash2 className="h-4 w-4" />
      </button>
    </div>
  );
}

// ─── Sub-component: Task Editor ──────────────────────────────────────────────

function TaskEditor({
  task,
  onSave,
  onCancel,
}: {
  task: ScheduledTask;
  onSave: (updated: ScheduledTask) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState(task.name);
  const [hour, setHour] = useState(task.hour);
  const [minute, setMinute] = useState(task.minute);
  const [repeat, setRepeat] = useState<RepeatMode>(task.repeat);

  const handleSave = () => {
    onSave({ ...task, name, hour, minute, repeat });
  };

  return (
    <div className="rounded-lg border border-cyan-500/30 bg-slate-900/80 p-4 space-y-3">
      {/* Name */}
      <div>
        <label className="mb-1 block text-xs font-medium text-cyan-400">任务名称</label>
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="输入任务名称…"
          className="w-full rounded border border-slate-600/50 bg-slate-800/60 px-3 py-1.5 text-sm text-cyan-100 placeholder-slate-500 outline-none focus:border-cyan-500/60 focus:ring-1 focus:ring-cyan-500/30"
        />
      </div>

      {/* Time */}
      <div className="flex gap-3">
        <div className="flex-1">
          <label className="mb-1 block text-xs font-medium text-cyan-400">小时 (0–23)</label>
          <input
            type="number"
            min={0}
            max={23}
            value={hour}
            onChange={(e) => setHour(Math.max(0, Math.min(23, parseInt(e.target.value) || 0)))}
            className="w-full rounded border border-slate-600/50 bg-slate-800/60 px-3 py-1.5 text-sm text-cyan-100 outline-none focus:border-cyan-500/60 focus:ring-1 focus:ring-cyan-500/30"
          />
        </div>
        <div className="flex-1">
          <label className="mb-1 block text-xs font-medium text-cyan-400">分钟 (0–59)</label>
          <input
            type="number"
            min={0}
            max={59}
            value={minute}
            onChange={(e) => setMinute(Math.max(0, Math.min(59, parseInt(e.target.value) || 0)))}
            className="w-full rounded border border-slate-600/50 bg-slate-800/60 px-3 py-1.5 text-sm text-cyan-100 outline-none focus:border-cyan-500/60 focus:ring-1 focus:ring-cyan-500/30"
          />
        </div>
      </div>

      {/* Repeat */}
      <div>
        <label className="mb-1 block text-xs font-medium text-cyan-400">重复</label>
        <div className="relative">
          <select
            value={repeat}
            onChange={(e) => setRepeat(e.target.value as RepeatMode)}
            className="w-full appearance-none rounded border border-slate-600/50 bg-slate-800/60 px-3 py-1.5 text-sm text-cyan-100 outline-none focus:border-cyan-500/60 focus:ring-1 focus:ring-cyan-500/30 pr-8"
          >
            {REPEAT_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
          <ChevronDown className="pointer-events-none absolute right-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
        </div>
      </div>

      {/* Preview */}
      <p className="text-xs text-slate-500">
        预览：每{repeat === "once" ? "次触发一次（不重复）" : repeat === "daily" ? "天" : REPEAT_OPTIONS.find((o) => o.value === repeat)?.label?.slice(2) ?? ""}{repeat !== "once" ? `，在 ${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")} 执行` : `，${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")} 触发`}
      </p>

      {/* Actions */}
      <div className="flex gap-2 pt-1">
        <button
          onClick={handleSave}
          className="flex items-center gap-1.5 rounded border border-cyan-500/40 bg-cyan-500/10 px-3 py-1.5 text-xs font-medium text-cyan-300 transition-colors hover:bg-cyan-500/20"
        >
          <Save className="h-3.5 w-3.5" />
          保存
        </button>
        <button
          onClick={onCancel}
          className="flex items-center gap-1.5 rounded border border-slate-600/40 bg-slate-700/30 px-3 py-1.5 text-xs font-medium text-slate-400 transition-colors hover:bg-slate-700/50"
        >
          取消
        </button>
      </div>
    </div>
  );
}

// ─── Main Component ──────────────────────────────────────────────────────────

function PmoRunStatusBadge({
  status,
  nowMs,
}: {
  status: PmoCopilotRunStatus;
  nowMs: number;
}) {
  if (status.phase === "idle") {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-slate-700/50 bg-slate-800/30 px-4 py-2.5 text-xs text-slate-500">
        <span className="h-2 w-2 rounded-full bg-slate-600" />
        <span>就绪 — 尚未运行 PMO 任务</span>
      </div>
    );
  }

  if (status.phase === "running") {
    const started = status.started_at_ms ?? nowMs;
    const elapsed = formatElapsedMs(nowMs - started);
    const taskHint = status.label?.includes("INIT") ? "INIT 数据更新" : "PMO 全流程";
    return (
      <div className="flex items-center gap-3 rounded-lg border border-cyan-500/35 bg-cyan-500/10 px-4 py-2.5">
        <Loader2 className="h-4 w-4 flex-shrink-0 animate-spin text-cyan-400" />
        <div className="min-w-0">
          <p className="text-sm font-medium text-cyan-200">{taskHint} · 运行中</p>
          <p className="text-[11px] text-cyan-400/80">
            已运行 {elapsed}
            {status.pid != null ? ` · PID ${status.pid}` : ""}
          </p>
        </div>
      </div>
    );
  }

  if (status.phase === "stopped") {
    const finishedAt = status.finished_at_ms ?? nowMs;
    const started = status.started_at_ms ?? finishedAt;
    const duration = formatElapsedMs(finishedAt - started);
    return (
      <div className="flex items-center gap-3 rounded-lg border border-amber-500/35 bg-amber-500/10 px-4 py-2.5">
        <Square className="h-4 w-4 flex-shrink-0 text-amber-400" />
        <div className="min-w-0">
          <p className="text-sm font-medium text-amber-200">PMO 已手动停止</p>
          <p className="text-[11px] text-amber-400/80">
            结束于 {formatClockFromMs(finishedAt)} · 已运行 {duration}
          </p>
        </div>
      </div>
    );
  }

  if (status.phase === "finished") {
    const finishedAt = status.finished_at_ms ?? nowMs;
    const started = status.started_at_ms ?? finishedAt;
    const duration = formatElapsedMs(finishedAt - started);
    return (
      <div className="flex items-center gap-3 rounded-lg border border-emerald-500/35 bg-emerald-500/10 px-4 py-2.5">
        <CheckCircle2 className="h-4 w-4 flex-shrink-0 text-emerald-400" />
        <div className="min-w-0">
          <p className="text-sm font-medium text-emerald-300">PMO 已完成</p>
          <p className="text-[11px] text-emerald-400/80">
            结束于 {formatClockFromMs(finishedAt)} · 耗时 {duration}
          </p>
        </div>
      </div>
    );
  }

  const finishedAt = status.finished_at_ms ?? nowMs;
  return (
    <div className="flex items-center gap-3 rounded-lg border border-rose-500/35 bg-rose-500/10 px-4 py-2.5">
      <XCircle className="h-4 w-4 flex-shrink-0 text-rose-400" />
      <div className="min-w-0">
        <p className="text-sm font-medium text-rose-300">PMO 运行失败</p>
        <p className="text-[11px] text-rose-400/80">
          结束于 {formatClockFromMs(finishedAt)}
          {status.exit_code != null ? ` · 退出码 ${status.exit_code}` : ""}
        </p>
      </div>
    </div>
  );
}

export function ProjectManagement() {
  const [launching, setLaunching] = useState(false);
  const [initLaunching, setInitLaunching] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [launchResult, setLaunchResult] = useState<{ ok: boolean; msg: string } | null>(null);
  const launchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const [pmoRunStatus, setPmoRunStatus] = useState<PmoCopilotRunStatus>({ phase: "idle" });
  const [statusNowMs, setStatusNowMs] = useState(() => Date.now());

  const [pmoConfigOpen, setPmoConfigOpen] = useState(false);
  const [pmoConfigPath, setPmoConfigPath] = useState("");
  const [pmoConfigYaml, setPmoConfigYaml] = useState("");
  const [pmoConfigLoading, setPmoConfigLoading] = useState(false);
  const [pmoConfigSaving, setPmoConfigSaving] = useState(false);
  const [pmoConfigError, setPmoConfigError] = useState<string | null>(null);
  const [pmoConfigSaved, setPmoConfigSaved] = useState(false);

  const [tasks, setTasks] = useState<ScheduledTask[]>(loadTasks);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [saveBanner, setSaveBanner] = useState<string | null>(null);
  const saveBannerTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Persist to localStorage whenever tasks change
  useEffect(() => {
    saveTasks(tasks);
  }, [tasks]);

  const refreshPmoRunStatus = useCallback(async () => {
    try {
      const s = await invoke<PmoCopilotRunStatus>("get_pmo_copilot_run_status");
      setPmoRunStatus(s);
    } catch {
      /* 桌面旧版无此命令时保持本地状态 */
    }
    setStatusNowMs(Date.now());
  }, []);

  // 轮询 PMO 后台进程状态（运行中 2s，其余 5s）
  useEffect(() => {
    void refreshPmoRunStatus();
    const intervalMs = pmoRunStatus.phase === "running" ? 2000 : 5000;
    const id = window.setInterval(() => void refreshPmoRunStatus(), intervalMs);
    return () => window.clearInterval(id);
  }, [pmoRunStatus.phase, refreshPmoRunStatus]);

  // 运行中时每秒刷新耗时显示
  useEffect(() => {
    if (pmoRunStatus.phase !== "running") return;
    const id = window.setInterval(() => setStatusNowMs(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [pmoRunStatus.phase]);

  const pmoIsRunning = pmoRunStatus.phase === "running";

  // Scheduler tick — checks every 30 seconds
  useEffect(() => {
    const check = () => {
      const now = new Date();
      setTasks((prev) => {
        let changed = false;
        const next = prev.map((t) => {
          if (shouldFire(t, now)) {
            void (async () => {
              try {
                await invoke<string>("launch_pmo_copilot_script");
                await refreshPmoRunStatus();
              } catch {
                /* silent — scheduler fires best-effort */
              }
            })();
            changed = true;
            return markRan(t, now);
          }
          return t;
        });
        return changed ? next : prev;
      });
    };
    check();
    const id = window.setInterval(check, 30_000);
    return () => window.clearInterval(id);
  }, [refreshPmoRunStatus]);

  // ── PMO Trigger ────────────────────────────────────────────────────────────

  const handleLaunch = useCallback(async () => {
    if (launching || initLaunching || pmoIsRunning) return;
    setLaunching(true);
    setLaunchResult(null);
    if (launchTimerRef.current) clearTimeout(launchTimerRef.current);
    try {
      const msg = await invoke<string>("launch_pmo_copilot_script", { initOnly: false });
      setLaunchResult({ ok: true, msg });
      await refreshPmoRunStatus();
    } catch (e) {
      setLaunchResult({ ok: false, msg: String(e) });
    } finally {
      setLaunching(false);
      launchTimerRef.current = setTimeout(() => setLaunchResult(null), 8000);
    }
  }, [launching, initLaunching, pmoIsRunning, refreshPmoRunStatus]);

  const handleInitUpdate = useCallback(async () => {
    if (launching || initLaunching || pmoIsRunning) return;
    setInitLaunching(true);
    setLaunchResult(null);
    if (launchTimerRef.current) clearTimeout(launchTimerRef.current);
    try {
      const msg = await invoke<string>("launch_pmo_copilot_script", { initOnly: true });
      setLaunchResult({ ok: true, msg });
      await refreshPmoRunStatus();
    } catch (e) {
      setLaunchResult({ ok: false, msg: String(e) });
    } finally {
      setInitLaunching(false);
      launchTimerRef.current = setTimeout(() => setLaunchResult(null), 8000);
    }
  }, [launching, initLaunching, pmoIsRunning, refreshPmoRunStatus]);

  const handleStop = useCallback(async () => {
    if (!pmoIsRunning || stopping) return;
    setStopping(true);
    setLaunchResult(null);
    if (launchTimerRef.current) clearTimeout(launchTimerRef.current);
    try {
      const msg = await invoke<string>("stop_pmo_copilot_run");
      setLaunchResult({ ok: true, msg });
      await refreshPmoRunStatus();
    } catch (e) {
      setLaunchResult({ ok: false, msg: String(e) });
    } finally {
      setStopping(false);
      launchTimerRef.current = setTimeout(() => setLaunchResult(null), 8000);
    }
  }, [pmoIsRunning, stopping, refreshPmoRunStatus]);

  const loadPmoConfig = useCallback(async () => {
    setPmoConfigLoading(true);
    setPmoConfigError(null);
    try {
      const info = await invoke<PmoSkillConfigInfo>("read_pmo_skill_config");
      setPmoConfigPath(info.path);
      setPmoConfigYaml(info.yaml);
      setPmoConfigOpen(true);
    } catch (e) {
      setPmoConfigError(String(e));
    } finally {
      setPmoConfigLoading(false);
    }
  }, []);

  const savePmoConfig = useCallback(async () => {
    setPmoConfigSaving(true);
    setPmoConfigError(null);
    try {
      const info = await invoke<PmoSkillConfigInfo>("write_pmo_skill_config", { yaml: pmoConfigYaml });
      setPmoConfigPath(info.path);
      setPmoConfigYaml(info.yaml);
      setPmoConfigSaved(true);
      setTimeout(() => setPmoConfigSaved(false), 3000);
    } catch (e) {
      setPmoConfigError(String(e));
    } finally {
      setPmoConfigSaving(false);
    }
  }, [pmoConfigYaml]);

  const openPmoConfigDir = useCallback(async () => {
    try {
      await invoke<string>("open_pmo_skill_config_dir");
    } catch (e) {
      setPmoConfigError(String(e));
    }
  }, []);

  // ── Task CRUD ──────────────────────────────────────────────────────────────

  const handleAddTask = useCallback(() => {
    const newTask: ScheduledTask = {
      id: genId(),
      name: "",
      hour: 9,
      minute: 0,
      repeat: "daily",
      enabled: true,
      lastRunDate: null,
    };
    setTasks((prev) => [...prev, newTask]);
    setEditingId(newTask.id);
  }, []);

  const handleToggle = useCallback((id: string) => {
    setTasks((prev) =>
      prev.map((t) => (t.id === id ? { ...t, enabled: !t.enabled } : t))
    );
  }, []);

  const handleDelete = useCallback(
    (id: string) => {
      setTasks((prev) => prev.filter((t) => t.id !== id));
      if (editingId === id) setEditingId(null);
    },
    [editingId]
  );

  const handleSaveTask = useCallback((updated: ScheduledTask) => {
    setTasks((prev) => prev.map((t) => (t.id === updated.id ? updated : t)));
    setEditingId(null);
    if (saveBannerTimerRef.current) clearTimeout(saveBannerTimerRef.current);
    setSaveBanner("定时任务已保存");
    saveBannerTimerRef.current = setTimeout(() => setSaveBanner(null), 2500);
  }, []);

  const handleCancelEdit = useCallback(() => {
    // If the task being cancelled is still blank (no name), remove it
    setTasks((prev) =>
      prev.filter((t) => t.id !== editingId || t.name.trim() !== "")
    );
    setEditingId(null);
  }, [editingId]);

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div className="flex h-full flex-col gap-6 overflow-auto p-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <div className="flex h-9 w-9 items-center justify-center rounded-lg border border-cyan-500/25 bg-gradient-to-br from-cyan-500/15 to-rose-500/10">
          <Briefcase className="h-5 w-5 text-cyan-400" />
        </div>
        <div>
          <h1 className="text-base font-semibold text-cyan-100" style={{ fontFamily: "Orbitron, sans-serif" }}>
            项目管理
          </h1>
          <p className="text-xs text-slate-500">PMO Copilot 触发器 · 定时任务调度</p>
        </div>
      </div>

      {/* ── Section 1: PMO Copilot Trigger ── */}
      <section className="rounded-xl border border-cyan-500/15 bg-slate-900/40 p-5">
        <div className="mb-4 flex items-center gap-2">
          <Terminal className="h-4 w-4 text-cyan-400" />
          <h2 className="text-sm font-semibold text-cyan-300">PMO Copilot</h2>
        </div>

        <p className="mb-4 text-xs leading-relaxed text-slate-400">
          点击下方按钮，将在<strong className="text-slate-300">后台</strong>启动 PMO 全流程（不弹命令窗）。
          开发机（<code className="text-cyan-300/90">start-layer3.ps1</code>）走{" "}
          <code className="rounded bg-slate-800 px-1 py-0.5 text-cyan-300 text-[11px]">
            python scripts/run_pmo_copilot_skill.py
          </code>
          ；「数据更新」等价加 <code className="text-cyan-300/90">--init</code>（拉表 + 入库）。
          安装包走侧车 <code className="text-cyan-300/90">--run-pmo-copilot</code>。
          日志见 <code className="text-cyan-300/90">logs/pmo_copilot_*.log</code>、
          <code className="text-cyan-300/90">logs/pmo/pmo_l3_debug.log</code> 与{" "}
          <code className="text-cyan-300/90">~/.jachin/jachin_debug/健康skill/pmo_copilot_*.txt</code>。
        </p>

        <div className="mb-4 flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => void loadPmoConfig()}
            disabled={pmoConfigLoading}
            className="flex items-center gap-1.5 rounded-lg border border-slate-600/50 bg-slate-800/50 px-3 py-2 text-xs text-slate-300 transition-colors hover:border-cyan-500/30 hover:text-cyan-200"
          >
            {pmoConfigLoading ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Settings className="h-3.5 w-3.5" />
            )}
            PMO 本地配置
          </button>
          <button
            type="button"
            onClick={() => void openPmoConfigDir()}
            className="flex items-center gap-1.5 rounded-lg border border-slate-600/50 bg-slate-800/50 px-3 py-2 text-xs text-slate-300 transition-colors hover:border-cyan-500/30 hover:text-cyan-200"
          >
            <FolderOpen className="h-3.5 w-3.5" />
            打开配置目录
          </button>
          {pmoConfigPath && (
            <span className="text-[10px] text-slate-500 truncate max-w-full" title={pmoConfigPath}>
              {pmoConfigPath}
            </span>
          )}
        </div>

        {pmoConfigOpen && (
          <div className="mb-4 rounded-lg border border-slate-700/60 bg-slate-950/50 p-3">
            <p className="mb-2 text-[11px] text-slate-400">
              编辑{" "}
              <code className="text-cyan-300/90">pmo_bitable_watch.yaml</code>
              （飞书多维表监控 / 变更预警）。保存后写入{" "}
              <code className="text-cyan-300/90">~/.jachin/config/skills/pmo-copilot/</code>
            </p>
            <textarea
              value={pmoConfigYaml}
              onChange={(e) => setPmoConfigYaml(e.target.value)}
              rows={14}
              spellCheck={false}
              className="w-full rounded-lg border border-white/10 bg-slate-900/80 px-3 py-2 font-mono text-[11px] leading-relaxed text-slate-200 focus:border-cyan-500/40 focus:outline-none"
            />
            <div className="mt-2 flex items-center gap-2">
              <button
                type="button"
                onClick={() => void savePmoConfig()}
                disabled={pmoConfigSaving}
                className="flex items-center gap-1.5 rounded-lg border border-cyan-500/40 bg-cyan-500/10 px-3 py-1.5 text-xs font-medium text-cyan-300 hover:bg-cyan-500/20 disabled:opacity-50"
              >
                {pmoConfigSaving ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Save className="h-3.5 w-3.5" />
                )}
                保存配置
              </button>
              {pmoConfigSaved && (
                <span className="text-xs text-emerald-400">已保存</span>
              )}
            </div>
          </div>
        )}

        {pmoConfigError && (
          <p className="mb-3 text-xs text-rose-400">{pmoConfigError}</p>
        )}

        <div className="mb-4">
          <PmoRunStatusBadge status={pmoRunStatus} nowMs={statusNowMs} />
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={() => void handleLaunch()}
            disabled={launching || initLaunching || pmoIsRunning}
            className={cn(
              "flex items-center gap-2 rounded-lg border px-5 py-2.5 text-sm font-semibold transition-all duration-200",
              launching || initLaunching || pmoIsRunning
                ? "cursor-not-allowed border-slate-600/40 bg-slate-800/40 text-slate-500"
                : "border-cyan-500/40 bg-gradient-to-r from-cyan-500/15 to-cyan-600/10 text-cyan-300 hover:border-cyan-400/60 hover:from-cyan-500/25 hover:to-cyan-600/20 hover:shadow-[0_0_20px_rgba(34,211,238,0.15)] active:scale-[0.98]"
            )}
          >
            {launching ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : pmoIsRunning ? (
              <Loader2 className="h-4 w-4 animate-spin text-slate-500" />
            ) : (
              <Play className="h-4 w-4" />
            )}
            {launching ? "启动中…" : pmoIsRunning ? "PMO 运行中…" : "启动 PMO Copilot"}
          </button>

          <button
            type="button"
            onClick={() => void handleInitUpdate()}
            disabled={launching || initLaunching || pmoIsRunning}
            title="拉取飞书多维表并镜像入库（run_pmo_copilot_skill.py --init）"
            className={cn(
              "flex items-center gap-2 rounded-lg border px-4 py-2.5 text-sm font-medium transition-all duration-200",
              launching || initLaunching || pmoIsRunning
                ? "cursor-not-allowed border-slate-600/40 bg-slate-800/40 text-slate-500"
                : "border-violet-500/35 bg-violet-500/10 text-violet-300 hover:border-violet-400/50 hover:bg-violet-500/15 active:scale-[0.98]"
            )}
          >
            {initLaunching ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Database className="h-4 w-4" />
            )}
            {initLaunching ? "更新中…" : "数据更新"}
          </button>

          {pmoIsRunning && (
            <button
              type="button"
              onClick={() => void handleStop()}
              disabled={stopping}
              className={cn(
                "flex items-center gap-2 rounded-lg border px-4 py-2.5 text-sm font-medium transition-all duration-200",
                stopping
                  ? "cursor-not-allowed border-slate-600/40 bg-slate-800/40 text-slate-500"
                  : "border-rose-500/40 bg-rose-500/10 text-rose-300 hover:border-rose-400/55 hover:bg-rose-500/15 active:scale-[0.98]"
              )}
            >
              {stopping ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Square className="h-4 w-4 fill-current" />
              )}
              {stopping ? "停止中…" : "停止"}
            </button>
          )}

          {launchResult && (
            <div
              className={cn(
                "flex items-center gap-1.5 rounded-lg border px-3 py-2 text-xs transition-all",
                launchResult.ok
                  ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-400"
                  : "border-rose-500/30 bg-rose-500/10 text-rose-400"
              )}
            >
              {launchResult.ok ? (
                <CheckCircle2 className="h-3.5 w-3.5 flex-shrink-0" />
              ) : (
                <XCircle className="h-3.5 w-3.5 flex-shrink-0" />
              )}
              <span className="max-w-[320px] truncate">{launchResult.msg}</span>
            </div>
          )}
        </div>
      </section>

      {/* ── Section 2: Scheduled Tasks ── */}
      <section className="flex min-h-0 flex-1 flex-col rounded-xl border border-cyan-500/15 bg-slate-900/40 p-5">
        {/* Section header */}
        <div className="mb-4 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <CalendarClock className="h-4 w-4 text-cyan-400" />
            <h2 className="text-sm font-semibold text-cyan-300">定时任务</h2>
            {tasks.length > 0 && (
              <span className="rounded-full border border-cyan-500/20 bg-cyan-500/10 px-2 py-0.5 text-[10px] text-cyan-400">
                {tasks.filter((t) => t.enabled).length} / {tasks.length} 启用
              </span>
            )}
          </div>
          <button
            onClick={handleAddTask}
            className="flex items-center gap-1.5 rounded-lg border border-cyan-500/30 bg-cyan-500/8 px-3 py-1.5 text-xs font-medium text-cyan-400 transition-colors hover:border-cyan-500/50 hover:bg-cyan-500/15"
          >
            <Plus className="h-3.5 w-3.5" />
            添加任务
          </button>
        </div>

        {/* Save banner */}
        {saveBanner && (
          <div className="mb-3 flex items-center gap-2 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-xs text-emerald-400">
            <CheckCircle2 className="h-3.5 w-3.5 flex-shrink-0" />
            {saveBanner}
          </div>
        )}

        {/* Task list */}
        <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-auto">
          {tasks.length === 0 ? (
            <div className="flex flex-1 flex-col items-center justify-center gap-2 py-12 text-slate-500">
              <CalendarClock className="h-10 w-10 opacity-20" />
              <p className="text-sm">暂无定时任务</p>
              <p className="text-xs">点击「添加任务」创建首个定时调度</p>
            </div>
          ) : (
            tasks.map((task) =>
              editingId === task.id ? (
                <TaskEditor
                  key={task.id}
                  task={task}
                  onSave={handleSaveTask}
                  onCancel={handleCancelEdit}
                />
              ) : (
                <TaskRow
                  key={task.id}
                  task={task}
                  onToggle={handleToggle}
                  onDelete={handleDelete}
                  onEdit={setEditingId}
                />
              )
            )
          )}
        </div>

        {/* Footer hint */}
        {tasks.length > 0 && (
          <p className="mt-3 text-[11px] text-slate-600">
            调度每 30 秒检查一次 · 定时任务在本应用运行期间有效 · 配置自动保存至本地
          </p>
        )}
      </section>
    </div>
  );
}
