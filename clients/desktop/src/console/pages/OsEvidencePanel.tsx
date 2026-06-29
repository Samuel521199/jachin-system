import { useCallback, useEffect, useMemo, useState } from "react";
import type { ComponentType } from "react";
import { convertFileSrc, invoke } from "@tauri-apps/api/core";
import {
  CheckCircle2,
  AlertCircle,
  Activity,
  ExternalLink,
  FileJson,
  FileText,
  FolderClock,
  Library,
  MonitorCheck,
  Play,
  RefreshCw,
  Search,
  Send,
  Square,
  SquareStack,
  Timer,
  Users,
} from "lucide-react";
import { cn } from "../../utils/cn";

type EvidenceEntry = {
  id: string;
  task: string;
  ok: boolean;
  detail: string;
  generated_at: number;
  evidence_path: string;
  evidence_panel_path?: string | null;
  report_path?: string | null;
  recipients: string[];
  apps: string[];
  screenshots: string[];
  files: string[];
  message_preview: string;
  diagnosis: string;
  intent?: {
    task_type?: string;
    confidence?: number;
    risk_level?: string;
    reasoning?: string[];
    missing_slots?: string[];
    slots?: Record<string, unknown>;
    raw_text?: string;
  } | null;
  route?: {
    ok?: boolean;
    tool_id?: string;
    workflow_id?: string;
    reason?: string;
    evidence_policy?: string;
    required_slots?: string[];
    missing_slots?: string[];
  } | null;
  clarification?: {
    should_ask?: boolean;
    question?: string;
    reason?: string;
  } | null;
  tool_result?: unknown | null;
  parser?: {
    decision?: string;
    rule?: { task_type?: string; confidence?: number };
    llm?: { enabled?: boolean; status?: string };
    disagreement?: Record<string, unknown>;
  } | null;
  memory?: Record<string, unknown> | null;
  template?: {
    id?: string;
    title?: string;
    workflow_id?: string;
    tool_id?: string;
    description?: string;
    evidence?: string[];
  } | null;
  mission_preview?: {
    title?: string;
    task_type?: string;
    confidence?: number;
    template_id?: string;
    workflow_id?: string;
    tool_id?: string;
    summary?: string;
    auto_execute?: boolean;
    requires_confirmation?: boolean;
    clarification_question?: string;
    evidence_expected?: string[];
    execution_policy?: Record<string, unknown>;
  } | null;
  capability_semantic?: {
    selected?: { id?: string; domain?: string; risk?: string; description?: string; workflow_id?: string } | null;
    reason?: string;
    confidence?: number;
    candidates?: Array<{ score?: number; reason?: string; matched_terms?: string[]; capability?: { id?: string; domain?: string; workflow_id?: string } }>;
  } | null;
  workflow_composition?: {
    workflow_id?: string;
    mode?: string;
    selected_capability_id?: string;
    risk?: string;
    reason?: string;
    evidence_expected?: string[];
    steps?: Array<{ stage?: string; capability_id?: string; action?: string; evidence?: string[] }>;
  } | null;
  control?: {
    status?: string;
    decision?: string;
    pending_id?: string;
    pending_path?: string;
    initial_user_input?: string;
    confirmed_at?: string;
    cancelled_at?: string;
    executed_at?: string;
    finished_at?: string;
    history?: Array<Record<string, unknown>>;
  } | null;
  plan_preview?: {
    summary?: string;
    risk_level?: string;
    auto_execute?: boolean;
    requires_confirmation?: boolean;
    confirmation_reason?: string;
    apps?: string[];
    files?: string[];
    recipients?: string[];
    steps?: Array<{ stage?: string; action?: string; evidence?: string[] }>;
  } | null;
  attempts?: Array<{
    attempt?: number;
    ok?: boolean;
    detail?: string;
    failure_class?: string;
    duration_ms?: number;
    retry_decision?: { should_retry?: boolean; reason?: string; safe_to_retry?: boolean; max_attempts?: number };
  }>;
  retry?: { should_retry?: boolean; reason?: string; safe_to_retry?: boolean; max_attempts?: number } | null;
  metrics?: {
    duration_ms?: number;
    attempt_count?: number;
    final_ok?: boolean;
    failure_class?: string;
    workflow_id?: string;
    tool_id?: string;
    task_type?: string;
  } | null;
  timeline: Array<{
    ts: string;
    stage: string;
    status: string;
    detail: string;
    screenshots: string[];
    files: string[];
    ocr_preview: string;
    checks: string[];
  }>;
};

type LaunchInput = {
  project_name?: string;
  project_path?: string;
  recipients?: string[];
  wait_seconds?: number;
  dry_run?: boolean;
  template_id?: string;
};

type LaunchResult = {
  ok: boolean;
  mode: string;
  out_dir: string;
  pid?: number | null;
  message: string;
};

type StopResult = {
  ok: boolean;
  pid?: number | null;
  out_dir?: string | null;
  message: string;
};

type PreflightResult = {
  ok: boolean;
  checks: Array<{
    name: string;
    ok: boolean;
    detail?: string;
    warning?: boolean;
  }>;
  raw: unknown;
};

type EvidenceConfig = {
  project_name: string;
  project_path: string;
  recipients: string[];
  wait_seconds: number;
  dry_run: boolean;
};

type EvidenceStats = {
  total: number;
  passed: number;
  failed: number;
  success_rate: number;
  avg_duration_ms: number;
  avg_attempts: number;
  failure_top: Array<[string, number]>;
  workflow_top: Array<[string, number]>;
  workflow_pass_rate: Array<[string, number, number, number]>;
};

type ActiveRun = {
  pid?: number | null;
  out_dir: string;
  mode: string;
  label: string;
  started_at: number;
};

const DEFAULT_PROJECT_NAME = "Jachin";
const DEFAULT_PROJECT_PATH = "D:\\Projects\\jachi\\jachin-system-main";
const DEMO_RECIPIENTS = ["Vivian", "Samuel", "测试备注冒烟草稿"];

const TASK_TEMPLATES = [
  {
    id: "router_codex_project_lark",
    title: "Mission Router 标准 Demo",
    detail: "按聊天输入链路识别项目简报任务，路由到 Codex -> Lark，并写入 Router Evidence。",
  },
  {
    id: "codex_project_lark",
    title: "Codex 项目简报发 Lark",
    detail: "Codex 读取项目并总结，再发送到 Lark。",
  },
  {
    id: "daily_office_briefing",
    title: "今日办公现场简报",
    detail: "扫描窗口、系统状态和最近文件，生成办公简报。",
  },
  {
    id: "recent_files",
    title: "最近文件整理",
    detail: "读取项目/办公目录最近变更文件并分类。",
  },
  {
    id: "app_switch_matrix",
    title: "打开多个 App 并验证",
    detail: "打开或切换 Codex、Lark、浏览器、资源管理器。",
  },
  {
    id: "project_memory",
    title: "项目路径记忆管理",
    detail: "把项目名和本机路径写入 OS 助手记忆。",
  },
];

function formatTime(sec: number) {
  if (!sec) return "-";
  return new Date(sec * 1000).toLocaleString();
}

function shortPath(path?: string | null) {
  if (!path) return "";
  const parts = path.replace(/\\/g, "/").split("/");
  if (parts.length <= 3) return path;
  return `${parts[parts.length - 3]}/${parts[parts.length - 2]}/${parts[parts.length - 1]}`;
}

async function openPath(path?: string | null) {
  if (!path) return;
  await invoke("os_evidence_open_path", { path });
}

function imageSrc(path: string) {
  try {
    return convertFileSrc(path);
  } catch {
    return path;
  }
}

function latestStep(item: EvidenceEntry) {
  return item.timeline[item.timeline.length - 1] ?? null;
}

function isRunning(item: EvidenceEntry) {
  const step = latestStep(item);
  return item.detail === "running" || step?.status === "running";
}

function elapsedText(item: EvidenceEntry, nowMs: number) {
  if (!item.generated_at) return "-";
  const seconds = Math.max(0, Math.round((nowMs - item.generated_at * 1000) / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${seconds % 60}s`;
}

function msText(ms?: number | null) {
  if (!ms) return "-";
  if (ms < 1000) return `${ms}ms`;
  const sec = ms / 1000;
  if (sec < 60) return `${sec.toFixed(1)}s`;
  return `${Math.floor(sec / 60)}m ${Math.round(sec % 60)}s`;
}

function nextStepHint(item: EvidenceEntry) {
  const stage = latestStep(item)?.stage ?? "";
  if (stage.includes("resolve")) return "打开 Codex";
  if (stage.includes("open_codex")) return "输入项目总结任务";
  if (stage.includes("paste") || stage.includes("submit")) return "等待 Codex 输出";
  if (stage.includes("wait_codex")) return "复制并校验 Codex 结果";
  if (stage.includes("validate")) return "打开 Lark 并发送";
  if (stage.includes("preview")) return "发送并做 OCR 校验";
  if (stage.includes("verify")) return "生成 Evidence 面板";
  return item.ok ? "已完成" : "等待下一步 evidence";
}

function sameRecipients(item: EvidenceEntry, recipients: string[]) {
  const actual = new Set(item.recipients.map((name) => name.toLowerCase()));
  return recipients.every((name) => actual.has(name.toLowerCase())) && actual.size === recipients.length;
}

function smokeRows(items: EvidenceEntry[]) {
  const scenarios = [
    { name: "Vivian", recipients: ["Vivian"] },
    { name: "Vivian + Samuel", recipients: ["Vivian", "Samuel"] },
    { name: "Vivian + 测试备注冒烟草稿", recipients: ["Vivian", "测试备注冒烟草稿"] },
  ];
  return scenarios.map((scenario) => {
    const match = items.find((item) => sameRecipients(item, scenario.recipients));
    return {
      ...scenario,
      item: match,
      status: match ? (match.ok ? "passed" : "check") : "pending",
    };
  });
}

export function OsEvidencePanel() {
  const [items, setItems] = useState<EvidenceEntry[]>([]);
  const [selectedId, setSelectedId] = useState<string>("");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [projectName, setProjectName] = useState(DEFAULT_PROJECT_NAME);
  const [projectPath, setProjectPath] = useState(DEFAULT_PROJECT_PATH);
  const [selectedRecipients, setSelectedRecipients] = useState<string[]>(["Vivian"]);
  const [waitSeconds, setWaitSeconds] = useState(120);
  const [dryRun, setDryRun] = useState(false);
  const [configLoaded, setConfigLoaded] = useState(false);
  const [activeRun, setActiveRun] = useState<ActiveRun | null>(() => {
    try {
      const raw = window.localStorage.getItem("jachin.osEvidence.activeRun");
      return raw ? (JSON.parse(raw) as ActiveRun) : null;
    } catch {
      return null;
    }
  });
  const [launching, setLaunching] = useState<string | null>(null);
  const [launchMessage, setLaunchMessage] = useState<string | null>(null);
  const [preflight, setPreflight] = useState<PreflightResult | null>(null);
  const [stats, setStats] = useState<EvidenceStats | null>(null);
  const [nowMs, setNowMs] = useState(() => Date.now());

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    setError(null);
    try {
      const rows = await invoke<EvidenceEntry[]>("os_evidence_list", { limit: 120 });
      setItems(rows);
      setSelectedId((prev) => (prev && rows.some((row) => row.id === prev) ? prev : rows[0]?.id ?? ""));
      void invoke<EvidenceStats>("os_evidence_stats", { limit: 300 })
        .then(setStats)
        .catch(() => setStats(null));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setItems([]);
      setSelectedId("");
    } finally {
      if (!quiet) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    let cancelled = false;
    void invoke<EvidenceConfig>("os_evidence_config_get")
      .then((config) => {
        if (cancelled) return;
        setProjectName(config.project_name || DEFAULT_PROJECT_NAME);
        setProjectPath(config.project_path || DEFAULT_PROJECT_PATH);
        setSelectedRecipients(config.recipients?.length ? config.recipients : ["Vivian"]);
        setWaitSeconds(config.wait_seconds || 120);
        setDryRun(Boolean(config.dry_run));
      })
      .catch((err) => {
        setLaunchMessage(`配置读取失败：${err instanceof Error ? err.message : String(err)}`);
      })
      .finally(() => {
        if (!cancelled) setConfigLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!configLoaded) return;
    const timer = window.setTimeout(() => {
      void invoke<EvidenceConfig>("os_evidence_config_set", {
        config: {
          project_name: projectName,
          project_path: projectPath,
          recipients: selectedRecipients,
          wait_seconds: waitSeconds,
          dry_run: dryRun,
        },
      }).catch((err) => {
        setLaunchMessage(`配置保存失败：${err instanceof Error ? err.message : String(err)}`);
      });
    }, 500);
    return () => window.clearTimeout(timer);
  }, [configLoaded, dryRun, projectName, projectPath, selectedRecipients, waitSeconds]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      void load(true);
    }, 5000);
    return () => window.clearInterval(timer);
  }, [load]);

  useEffect(() => {
    const timer = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (activeRun) {
      window.localStorage.setItem("jachin.osEvidence.activeRun", JSON.stringify(activeRun));
    } else {
      window.localStorage.removeItem("jachin.osEvidence.activeRun");
    }
  }, [activeRun]);

  const launch = useCallback(
    async (command: string, input: LaunchInput, label: string) => {
      setLaunching(label);
      setLaunchMessage(null);
      try {
        const result = await invoke<LaunchResult>(command, { input });
        setLaunchMessage(`${label} 已启动，pid=${result.pid ?? "-"}，证据目录：${result.out_dir}`);
        setActiveRun({
          pid: result.pid,
          out_dir: result.out_dir,
          mode: result.mode,
          label,
          started_at: Date.now(),
        });
        await load(true);
      } catch (err) {
        setLaunchMessage(`${label} 启动失败：${err instanceof Error ? err.message : String(err)}`);
      } finally {
        setLaunching(null);
      }
    },
    [load],
  );

  const baseInput = useMemo<LaunchInput>(
    () => ({
      project_name: projectName.trim() || DEFAULT_PROJECT_NAME,
      project_path: projectPath.trim(),
      recipients: selectedRecipients,
      wait_seconds: waitSeconds,
      dry_run: dryRun,
    }),
    [dryRun, projectName, projectPath, selectedRecipients, waitSeconds],
  );

  const runStandardDemo = useCallback(async () => {
    setLaunching("前置检查");
    setLaunchMessage("正在检查 Codex、Lark、项目路径和收件人...");
    setPreflight(null);
    try {
      const check = await invoke<PreflightResult>("os_evidence_preflight", { input: baseInput });
      setPreflight(check);
      if (!check.ok) {
        setLaunchMessage("前置检查未通过，已阻止启动标准 Demo。");
        return;
      }
      await launch("os_evidence_start_standard_demo", baseInput, "标准 Demo");
    } catch (err) {
      setLaunchMessage(`前置检查失败：${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setLaunching(null);
    }
  }, [baseInput, launch]);

  const stopActiveRun = useCallback(async () => {
    if (!activeRun?.pid) {
      setLaunchMessage("没有可停止的任务 PID。");
      return;
    }
    setLaunching("停止任务");
    try {
      const result = await invoke<StopResult>("os_evidence_stop_task", {
        pid: activeRun.pid,
        outDir: activeRun.out_dir,
      });
      setLaunchMessage(result.ok ? `已停止任务 pid=${result.pid}` : `停止失败：${result.message}`);
      if (result.ok) setActiveRun(null);
      await load(true);
    } catch (err) {
      setLaunchMessage(`停止失败：${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setLaunching(null);
    }
  }, [activeRun, load]);

  const toggleRecipient = useCallback((name: string) => {
    setSelectedRecipients((prev) => {
      if (prev.includes(name)) return prev.filter((item) => item !== name);
      return [...prev, name];
    });
  }, []);

  const filtered = useMemo(() => {
    const key = query.trim().toLowerCase();
    if (!key) return items;
    return items.filter((item) =>
      [
        item.task,
        item.detail,
        item.message_preview,
        item.evidence_path,
        item.report_path ?? "",
        item.evidence_panel_path ?? "",
        ...item.recipients,
        ...item.apps,
        ...item.files,
      ]
        .join(" ")
        .toLowerCase()
        .includes(key),
    );
  }, [items, query]);

  const selected = filtered.find((item) => item.id === selectedId) ?? filtered[0] ?? null;
  const passed = items.filter((item) => item.ok).length;
  const failed = items.length - passed;
  const timelineCount = items.reduce((sum, item) => sum + (item.timeline?.length ?? 0), 0);
  const runningItems = items.filter(isRunning);
  const runCenterItems = runningItems.length > 0 ? runningItems.slice(0, 4) : items.slice(0, 3);
  const smoke = smokeRows(items);
  const smokePassed = smoke.filter((row) => row.status === "passed").length;

  return (
    <div className="flex min-h-full flex-col gap-4 p-6 text-slate-100">
      <header className="flex flex-col gap-4 border-b border-cyan-500/15 pb-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <div className="text-xs uppercase tracking-[0.22em] text-cyan-300/70">OS Assistant</div>
          <h1 className="mt-1 text-2xl font-semibold text-cyan-50">Evidence Console</h1>
          <p className="mt-2 max-w-3xl text-sm text-slate-400">
            跨 App 任务的执行记录、发送对象、报告与视觉校验集中视图。
          </p>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          disabled={loading}
          className="inline-flex items-center justify-center gap-2 rounded-md border border-cyan-400/25 bg-cyan-400/10 px-3 py-2 text-sm text-cyan-100 transition hover:bg-cyan-400/15 disabled:opacity-50"
        >
          <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
          刷新
        </button>
      </header>

      <section className="grid gap-3 md:grid-cols-4">
        <Metric label="执行记录" value={items.length} icon={SquareStack} tone="cyan" />
        <Metric label="通过" value={passed} icon={CheckCircle2} tone="green" />
        <Metric label="需检查" value={failed} icon={AlertCircle} tone="amber" />
        <Metric label="时间线步骤" value={timelineCount} icon={Send} tone="rose" />
      </section>

      <section className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(280px,0.7fr)]">
        <div className="rounded-lg border border-cyan-500/15 bg-slate-950/45 p-4">
          <div className="text-sm font-medium text-slate-200">运行质量</div>
          <div className="mt-3 grid grid-cols-3 gap-3 text-xs">
            <div>
              <div className="text-slate-500">成功率</div>
              <div className="mt-1 text-lg font-semibold text-emerald-300">{stats ? `${Math.round(stats.success_rate * 100)}%` : "-"}</div>
            </div>
            <div>
              <div className="text-slate-500">平均耗时</div>
              <div className="mt-1 text-lg font-semibold text-cyan-100">{msText(stats?.avg_duration_ms)}</div>
            </div>
            <div>
              <div className="text-slate-500">平均尝试</div>
              <div className="mt-1 text-lg font-semibold text-cyan-100">{stats ? stats.avg_attempts.toFixed(1) : "-"}</div>
            </div>
          </div>
        </div>
        <StatsList title="失败原因 Top" rows={stats?.failure_top ?? []} empty="暂无失败记录" />
        <StatsList title="Workflow 通过率" rows={(stats?.workflow_pass_rate ?? []).map(([name, total, pass, rate]) => [`${name} ${pass}/${total}`, Math.round(rate * 100)])} empty="暂无 workflow 统计" suffix="%" />
      </section>

      <section className="grid gap-4 xl:grid-cols-[minmax(0,1.15fr)_minmax(360px,0.85fr)]">
        <div className="rounded-lg border border-cyan-500/15 bg-slate-950/50 p-4">
          <div className="mb-3 flex items-center justify-between gap-3">
            <div className="flex items-center gap-2 text-sm font-medium text-slate-100">
              <Activity className="h-4 w-4 text-cyan-300" />
              任务运行中心
            </div>
            <span className="text-xs text-slate-500">{runningItems.length > 0 ? "运行中" : "空闲"}</span>
          </div>
          {runCenterItems.length === 0 ? (
            <div className="rounded-md border border-slate-800 bg-slate-900/45 p-4 text-sm text-slate-500">
              当前没有运行中的 OS 任务。启动 Demo 后，这里会显示当前步骤、耗时、下一步和证据。
            </div>
          ) : (
            <div className="grid gap-3">
              {runCenterItems.map((item) => {
                const step = latestStep(item);
                return (
                  <div key={item.id} className="rounded-md border border-cyan-500/20 bg-cyan-400/[0.06] p-4">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div className="min-w-0">
                        <div className="truncate text-sm font-medium text-cyan-50">{item.task}</div>
                        <div className="mt-1 text-xs text-slate-400">{step?.stage ?? item.detail}</div>
                      </div>
                      <div className="flex items-center gap-2">
                        <span
                          className={cn(
                            "rounded px-2 py-1 text-xs",
                            isRunning(item) && "bg-cyan-400/10 text-cyan-200",
                            !isRunning(item) && item.ok && "bg-emerald-400/10 text-emerald-300",
                            !isRunning(item) && !item.ok && "bg-amber-400/10 text-amber-300",
                          )}
                        >
                          {isRunning(item) ? "RUNNING" : item.ok ? "DONE" : "CHECK"}
                        </span>
                        <span className="inline-flex items-center gap-1 rounded bg-cyan-400/10 px-2 py-1 text-xs text-cyan-200">
                          <Timer className="h-3.5 w-3.5" />
                          {elapsedText(item, nowMs)}
                        </span>
                      </div>
                    </div>
                    <div className="mt-3 grid gap-2 text-xs text-slate-400 md:grid-cols-3">
                      <div>下一步：{nextStepHint(item)}</div>
                      <div>发送对象：{item.recipients.join("、") || "-"}</div>
                      <div>截图证据：{shortPath(item.screenshots[0]) || "-"}</div>
                    </div>
                    {!isRunning(item) ? <div className="mt-2 text-xs text-slate-500">{item.diagnosis}</div> : null}
                  </div>
                );
              })}
            </div>
          )}
        </div>

        <div className="rounded-lg border border-cyan-500/15 bg-slate-950/50 p-4">
          <div className="mb-3 flex items-center gap-2 text-sm font-medium text-slate-100">
            <Play className="h-4 w-4 text-cyan-300" />
            一键 Demo
          </div>
          <div className="grid gap-3">
            <div className="grid gap-2 sm:grid-cols-[120px_minmax(0,1fr)]">
              <label className="text-xs text-slate-500">项目名</label>
              <input
                value={projectName}
                onChange={(event) => setProjectName(event.target.value)}
                className="rounded-md border border-slate-800 bg-slate-900 px-3 py-2 text-sm text-slate-200 outline-none focus:border-cyan-500/50"
              />
              <label className="text-xs text-slate-500">项目路径</label>
              <input
                value={projectPath}
                onChange={(event) => setProjectPath(event.target.value)}
                className="rounded-md border border-slate-800 bg-slate-900 px-3 py-2 text-sm text-slate-200 outline-none focus:border-cyan-500/50"
              />
              <label className="text-xs text-slate-500">等待秒数</label>
              <input
                type="number"
                min={10}
                max={600}
                value={waitSeconds}
                onChange={(event) => setWaitSeconds(Math.max(10, Math.min(600, Number(event.target.value) || 120)))}
                className="rounded-md border border-slate-800 bg-slate-900 px-3 py-2 text-sm text-slate-200 outline-none focus:border-cyan-500/50"
              />
            </div>
            <div className="flex flex-wrap gap-2">
              {DEMO_RECIPIENTS.map((name) => (
                <button
                  key={name}
                  type="button"
                  onClick={() => toggleRecipient(name)}
                  className={cn(
                    "rounded-md border px-3 py-1.5 text-xs transition",
                    selectedRecipients.includes(name)
                      ? "border-rose-300/40 bg-rose-400/15 text-rose-100"
                      : "border-slate-800 bg-slate-900 text-slate-500 hover:text-slate-200",
                  )}
                >
                  {name}
                </button>
              ))}
            </div>
            <label className="flex items-center gap-2 text-xs text-slate-400">
              <input
                type="checkbox"
                checked={dryRun}
                onChange={(event) => setDryRun(event.target.checked)}
                className="h-4 w-4 accent-cyan-400"
              />
              Dry-run：生成证据但不发送 Lark
            </label>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                disabled={Boolean(launching) || selectedRecipients.length === 0}
                onClick={() => void runStandardDemo()}
                className="inline-flex items-center gap-2 rounded-md border border-cyan-400/25 bg-cyan-400/10 px-3 py-2 text-sm text-cyan-100 transition hover:bg-cyan-400/15 disabled:opacity-45"
              >
                <Play className="h-4 w-4" />
                运行标准 Demo
              </button>
              <button
                type="button"
                disabled={Boolean(launching)}
                onClick={() => void launch("os_evidence_start_smoke_matrix", { ...baseInput, recipients: DEMO_RECIPIENTS }, "多收件人烟测矩阵")}
                className="inline-flex items-center gap-2 rounded-md border border-rose-400/25 bg-rose-400/10 px-3 py-2 text-sm text-rose-100 transition hover:bg-rose-400/15 disabled:opacity-45"
              >
                <Users className="h-4 w-4" />
                运行烟测矩阵
              </button>
              <button
                type="button"
                disabled={!activeRun?.pid || Boolean(launching)}
                onClick={() => void stopActiveRun()}
                className="inline-flex items-center gap-2 rounded-md border border-amber-400/25 bg-amber-400/10 px-3 py-2 text-sm text-amber-100 transition hover:bg-amber-400/15 disabled:opacity-45"
              >
                <Square className="h-4 w-4" />
                停止当前任务
              </button>
            </div>
            {activeRun ? (
              <div className="text-xs text-slate-500">
                当前任务：{activeRun.label}，pid={activeRun.pid ?? "-"}，目录：{shortPath(activeRun.out_dir)}
              </div>
            ) : null}
            {launchMessage ? <div className="text-xs text-slate-400">{launchMessage}</div> : null}
            {preflight ? (
              <div className="rounded-md border border-slate-800 bg-slate-900/45 p-3">
                <div className="mb-2 text-xs font-medium text-slate-300">前置检查</div>
                <div className="space-y-1">
                  {preflight.checks.map((check) => (
                    <div key={check.name} className="flex items-start justify-between gap-3 text-xs">
                      <span className="text-slate-400">{check.name}</span>
                      <span
                        className={cn(
                          "rounded px-2 py-0.5",
                          check.ok && "bg-emerald-400/10 text-emerald-300",
                          !check.ok && check.warning && "bg-amber-400/10 text-amber-300",
                          !check.ok && !check.warning && "bg-rose-400/10 text-rose-300",
                        )}
                      >
                        {check.ok ? "OK" : check.warning ? "WARN" : "FAIL"}
                      </span>
                      <span className="min-w-0 flex-1 truncate text-right text-slate-600">{check.detail ?? ""}</span>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}
          </div>
        </div>
      </section>

      <section className="grid gap-4 xl:grid-cols-[360px_minmax(0,1fr)]">
        <div className="rounded-lg border border-cyan-500/15 bg-slate-950/50 p-4">
          <div className="mb-3 flex items-center justify-between">
            <div className="flex items-center gap-2 text-sm font-medium text-slate-100">
              <Users className="h-4 w-4 text-cyan-300" />
              多收件人烟测矩阵
            </div>
            <span className="text-xs text-slate-500">{smokePassed}/{smoke.length}</span>
          </div>
          <div className="space-y-2">
            {smoke.map((row) => (
              <div key={row.name} className="rounded-md border border-slate-800 bg-slate-900/45 p-3">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-sm text-slate-200">{row.name}</span>
                  <span
                    className={cn(
                      "rounded px-2 py-0.5 text-xs",
                      row.status === "passed" && "bg-emerald-400/10 text-emerald-300",
                      row.status === "check" && "bg-amber-400/10 text-amber-300",
                      row.status === "pending" && "bg-slate-800 text-slate-500",
                    )}
                  >
                    {row.status}
                  </span>
                </div>
                <div className="mt-1 text-xs text-slate-500">{row.item?.diagnosis ?? "等待产生 evidence"}</div>
              </div>
            ))}
          </div>
        </div>

        <div className="rounded-lg border border-cyan-500/15 bg-slate-950/50 p-4">
          <div className="mb-3 flex items-center gap-2 text-sm font-medium text-slate-100">
            <Library className="h-4 w-4 text-cyan-300" />
            任务模板库
          </div>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {TASK_TEMPLATES.map((template) => (
              <button
                key={template.id}
                type="button"
                disabled={Boolean(launching)}
                onClick={() => void launch("os_evidence_start_template", { ...baseInput, template_id: template.id }, template.title)}
                className="min-h-[112px] rounded-md border border-slate-800 bg-slate-900/45 p-4 text-left transition hover:border-cyan-500/30 hover:bg-slate-900 disabled:opacity-45"
              >
                <div className="flex items-center gap-2 text-sm font-medium text-slate-100">
                  <FolderClock className="h-4 w-4 text-cyan-300" />
                  {template.title}
                </div>
                <div className="mt-2 text-xs leading-5 text-slate-500">{template.detail}</div>
              </button>
            ))}
          </div>
        </div>
      </section>

      <section className="grid min-h-[560px] flex-1 gap-4 lg:grid-cols-[420px_minmax(0,1fr)]">
        <div className="flex min-h-0 flex-col rounded-lg border border-cyan-500/15 bg-slate-950/50">
          <div className="border-b border-cyan-500/10 p-3">
            <label className="flex items-center gap-2 rounded-md border border-cyan-500/15 bg-slate-900/80 px-3 py-2 text-sm text-slate-300">
              <Search className="h-4 w-4 text-cyan-300/70" />
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="搜索任务、对象、文件"
                className="min-w-0 flex-1 bg-transparent outline-none placeholder:text-slate-600"
              />
            </label>
          </div>
          <div className="min-h-0 flex-1 overflow-auto p-2">
            {error ? <div className="p-4 text-sm text-rose-300">{error}</div> : null}
            {!error && filtered.length === 0 ? (
              <div className="p-4 text-sm text-slate-500">暂无 evidence 记录。</div>
            ) : null}
            {filtered.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => setSelectedId(item.id)}
                className={cn(
                  "mb-2 w-full rounded-md border p-3 text-left transition",
                  selected?.id === item.id
                    ? "border-cyan-400/35 bg-cyan-400/[0.09]"
                    : "border-slate-800 bg-slate-900/45 hover:border-cyan-500/25 hover:bg-slate-900/80",
                )}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-medium text-slate-100">{item.task}</div>
                    <div className="mt-1 truncate text-xs text-slate-500">{formatTime(item.generated_at)}</div>
                  </div>
                  <span
                    className={cn(
                      "shrink-0 rounded px-2 py-1 text-xs",
                      item.ok ? "bg-emerald-400/10 text-emerald-300" : "bg-amber-400/10 text-amber-300",
                    )}
                  >
                    {item.ok ? "OK" : "CHECK"}
                  </span>
                </div>
                <div className="mt-2 line-clamp-2 text-xs text-slate-400">{item.detail}</div>
                {item.recipients.length > 0 ? (
                  <div className="mt-3 flex flex-wrap gap-1">
                    {item.recipients.slice(0, 4).map((recipient) => (
                      <span key={recipient} className="rounded bg-rose-400/10 px-2 py-0.5 text-xs text-rose-200">
                        {recipient}
                      </span>
                    ))}
                  </div>
                ) : null}
              </button>
            ))}
          </div>
        </div>

        <div className="min-w-0 rounded-lg border border-cyan-500/15 bg-slate-950/50">
          {selected ? <EvidenceDetail item={selected} /> : <div className="p-6 text-sm text-slate-500">选择一条记录查看详情。</div>}
        </div>
      </section>
    </div>
  );
}

function Metric({
  label,
  value,
  icon: Icon,
  tone,
}: {
  label: string;
  value: number;
  icon: ComponentType<{ className?: string }>;
  tone: "cyan" | "green" | "amber" | "rose";
}) {
  const toneClass = {
    cyan: "text-cyan-300 bg-cyan-400/10 border-cyan-400/20",
    green: "text-emerald-300 bg-emerald-400/10 border-emerald-400/20",
    amber: "text-amber-300 bg-amber-400/10 border-amber-400/20",
    rose: "text-rose-300 bg-rose-400/10 border-rose-400/20",
  }[tone];
  return (
    <div className="rounded-lg border border-cyan-500/15 bg-slate-950/45 p-4">
      <div className="flex items-center justify-between">
        <span className="text-sm text-slate-400">{label}</span>
        <span className={cn("rounded-md border p-2", toneClass)}>
          <Icon className="h-4 w-4" />
        </span>
      </div>
      <div className="mt-3 text-2xl font-semibold text-slate-50">{value}</div>
    </div>
  );
}

function EvidenceDetail({ item }: { item: EvidenceEntry }) {
  const actions = [
    { label: "打开面板", path: item.evidence_panel_path, icon: MonitorCheck },
    { label: "打开报告", path: item.report_path, icon: FileText },
    { label: "打开 JSON", path: item.evidence_path, icon: FileJson },
  ];
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="border-b border-cyan-500/10 p-5">
        <div className="flex flex-col gap-3 xl:flex-row xl:items-start xl:justify-between">
          <div className="min-w-0">
            <div className="text-xs uppercase tracking-[0.2em] text-cyan-300/65">{formatTime(item.generated_at)}</div>
            <h2 className="mt-1 truncate text-xl font-semibold text-cyan-50">{item.task}</h2>
            <div className="mt-2 text-sm text-slate-400">{item.detail}</div>
          </div>
          <div className="flex flex-wrap gap-2">
            {actions.map(({ label, path, icon: Icon }) => (
              <button
                key={label}
                type="button"
                disabled={!path}
                onClick={() => void openPath(path)}
                className="inline-flex items-center gap-2 rounded-md border border-cyan-400/20 bg-cyan-400/10 px-3 py-2 text-sm text-cyan-100 transition hover:bg-cyan-400/15 disabled:cursor-not-allowed disabled:opacity-40"
              >
                <Icon className="h-4 w-4" />
                {label}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="grid gap-4 p-5 xl:grid-cols-2">
        <InfoBlock title="发送对象" items={item.recipients} empty="未发送或未记录对象" />
        <InfoBlock title="打开/识别到的 App" items={item.apps} empty="未记录 App" />
      </div>

      {item.mission_preview || item.template || item.memory ? (
        <div className="px-5 pb-5">
          <TaskPreviewBlock item={item} />
        </div>
      ) : null}

      {item.capability_semantic || item.workflow_composition ? (
        <div className="px-5 pb-5">
          <CapabilitySemanticBlock item={item} />
        </div>
      ) : null}

      {item.intent || item.route || item.clarification ? (
        <div className="px-5 pb-5">
          <MissionRouterBlock item={item} />
        </div>
      ) : null}

      {item.plan_preview ? (
        <div className="px-5 pb-5">
          <PlanPreviewBlock item={item} />
        </div>
      ) : null}

      {item.metrics || item.attempts?.length ? (
        <div className="px-5 pb-5">
          <RuntimeBlock item={item} />
        </div>
      ) : null}

      <div className="px-5 pb-5">
        <div className="rounded-lg border border-slate-800 bg-slate-900/45 p-4">
          <div className="mb-2 text-sm font-medium text-slate-200">输出摘要</div>
          <p className="whitespace-pre-wrap text-sm leading-6 text-slate-400">{item.message_preview || "无摘要"}</p>
        </div>
      </div>

      <div className="px-5 pb-5">
        <div
          className={cn(
            "rounded-lg border p-4",
            item.ok ? "border-emerald-400/15 bg-emerald-400/[0.06]" : "border-amber-400/20 bg-amber-400/[0.06]",
          )}
        >
          <div className="mb-2 text-sm font-medium text-slate-200">失败诊断 / 完成依据</div>
          <p className="text-sm leading-6 text-slate-400">{item.diagnosis || item.detail}</p>
        </div>
      </div>

      <div className="px-5 pb-5">
        <Timeline rows={item.timeline ?? []} />
      </div>

      <div className="px-5 pb-5">
        <ScreenshotStrip paths={item.screenshots} />
      </div>

      <div className="grid min-h-0 flex-1 gap-4 px-5 pb-5 xl:grid-cols-2">
        <PathList title="证据文件" paths={item.files} />
        <PathList title="截图/OCR" paths={item.screenshots} />
      </div>
    </div>
  );
}

function StatsList({ title, rows, empty, suffix = "" }: { title: string; rows: Array<[string, number]>; empty: string; suffix?: string }) {
  return (
    <div className="rounded-lg border border-cyan-500/15 bg-slate-950/45 p-4">
      <div className="mb-3 text-sm font-medium text-slate-200">{title}</div>
      {rows.length === 0 ? <div className="text-sm text-slate-500">{empty}</div> : null}
      <div className="space-y-2">
        {rows.slice(0, 5).map(([name, value]) => (
          <div key={name} className="flex items-center justify-between gap-3 text-xs">
            <span className="min-w-0 truncate text-slate-400">{name}</span>
            <span className="rounded bg-slate-800 px-2 py-0.5 text-cyan-200">
              {value}
              {suffix}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function slotEntries(slots?: Record<string, unknown>) {
  if (!slots) return [];
  return Object.entries(slots)
    .filter(([, value]) => {
      if (Array.isArray(value)) return value.length > 0;
      return value !== null && value !== undefined && String(value).trim() !== "";
    })
    .map(([key, value]) => [key, Array.isArray(value) ? value.join("、") : String(value)] as const);
}

function TaskPreviewBlock({ item }: { item: EvidenceEntry }) {
  const preview = item.mission_preview;
  const memory = item.memory ?? {};
  const memoryRows = Object.entries(memory)
    .filter(([, value]) => value !== null && value !== undefined && typeof value !== "object" && String(value).trim() !== "")
    .slice(0, 6);
  const evidence = preview?.evidence_expected ?? item.template?.evidence ?? [];
  return (
    <div className="rounded-lg border border-emerald-400/20 bg-emerald-400/[0.06] p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium text-emerald-50">Task Preview</span>
        {preview?.task_type ? <span className="rounded bg-emerald-400/10 px-2 py-0.5 text-xs text-emerald-200">{preview.task_type}</span> : null}
        {preview?.template_id || item.template?.id ? (
          <span className="rounded bg-slate-800 px-2 py-0.5 text-xs text-slate-300">{preview?.template_id || item.template?.id}</span>
        ) : null}
        <span
          className={cn(
            "rounded px-2 py-0.5 text-xs",
            preview?.requires_confirmation ? "bg-amber-400/10 text-amber-300" : "bg-cyan-400/10 text-cyan-200",
          )}
        >
          {preview?.requires_confirmation ? "confirm" : preview?.auto_execute === false ? "manual" : "auto"}
        </span>
      </div>
      <p className="text-sm leading-6 text-slate-400">{preview?.summary || item.template?.description || "-"}</p>
      <div className="mt-3 grid gap-3 md:grid-cols-3">
        <InfoMini title="Workflow" items={[preview?.workflow_id || item.template?.workflow_id || item.route?.workflow_id || "-"]} />
        <InfoMini title="Tool" items={[preview?.tool_id || item.template?.tool_id || item.route?.tool_id || "-"]} />
        <InfoMini title="Expected Evidence" items={evidence} />
      </div>
      <div className="mt-3 rounded-md border border-slate-800 bg-slate-950/45 p-3">
        <div className="mb-2 text-xs font-medium text-slate-300">Memory</div>
        {memoryRows.length === 0 ? <div className="text-xs text-slate-500">No simple memory fields recorded</div> : null}
        <div className="grid gap-1 text-xs md:grid-cols-2">
          {memoryRows.map(([key, value]) => (
            <div key={key} className="grid grid-cols-[140px_minmax(0,1fr)] gap-2">
              <span className="text-slate-500">{key}</span>
              <span className="min-w-0 break-words text-slate-300">{String(value)}</span>
            </div>
          ))}
        </div>
        {preview?.clarification_question ? <div className="mt-2 text-xs text-amber-300">{preview.clarification_question}</div> : null}
      </div>
      {item.control ? (
        <div className="mt-3 rounded-md border border-slate-800 bg-slate-950/45 p-3">
          <div className="mb-2 text-xs font-medium text-slate-300">Execution Control</div>
          <div className="grid gap-1 text-xs md:grid-cols-2">
            <div className="grid grid-cols-[140px_minmax(0,1fr)] gap-2">
              <span className="text-slate-500">status</span>
              <span className="min-w-0 break-words text-slate-300">{item.control.status || "-"}</span>
            </div>
            <div className="grid grid-cols-[140px_minmax(0,1fr)] gap-2">
              <span className="text-slate-500">decision</span>
              <span className="min-w-0 break-words text-slate-300">{item.control.decision || "-"}</span>
            </div>
            <div className="grid grid-cols-[140px_minmax(0,1fr)] gap-2">
              <span className="text-slate-500">pending_id</span>
              <span className="min-w-0 break-words text-slate-300">{item.control.pending_id || "-"}</span>
            </div>
            <div className="grid grid-cols-[140px_minmax(0,1fr)] gap-2">
              <span className="text-slate-500">finished_at</span>
              <span className="min-w-0 break-words text-slate-300">{item.control.finished_at || item.control.executed_at || item.control.cancelled_at || "-"}</span>
            </div>
          </div>
          {item.control.initial_user_input ? <div className="mt-2 text-xs text-slate-500">input: {item.control.initial_user_input}</div> : null}
          {item.control.history?.length ? (
            <div className="mt-2 space-y-1">
              {item.control.history.slice(-4).map((row, index) => (
                <div key={index} className="rounded bg-slate-900 px-2 py-1 text-[11px] text-slate-500">
                  {JSON.stringify(row)}
                </div>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function CapabilitySemanticBlock({ item }: { item: EvidenceEntry }) {
  const selected = item.capability_semantic?.selected;
  const candidates = item.capability_semantic?.candidates ?? [];
  const composition = item.workflow_composition;
  return (
    <div className="rounded-lg border border-violet-400/20 bg-violet-400/[0.06] p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium text-violet-50">Capability Semantic Router v2</span>
        {selected?.id ? <span className="rounded bg-violet-400/10 px-2 py-0.5 text-xs text-violet-200">{selected.id}</span> : null}
        {composition?.workflow_id ? <span className="rounded bg-slate-800 px-2 py-0.5 text-xs text-slate-300">{composition.workflow_id}</span> : null}
      </div>
      <p className="text-sm leading-6 text-slate-400">{selected?.description || composition?.reason || item.capability_semantic?.reason || "-"}</p>
      <div className="mt-3 grid gap-3 md:grid-cols-3">
        <InfoMini title="Selected Domain" items={[selected?.domain || "-"]} />
        <InfoMini title="Risk" items={[composition?.risk || selected?.risk || "-"]} />
        <InfoMini title="Mode" items={[composition?.mode || "-"]} />
      </div>
      {candidates.length ? (
        <div className="mt-3 rounded-md border border-slate-800 bg-slate-950/45 p-3">
          <div className="mb-2 text-xs font-medium text-slate-300">Top Candidates</div>
          <div className="space-y-2">
            {candidates.slice(0, 5).map((candidate, index) => (
              <div key={`${candidate.capability?.id || index}`} className="grid grid-cols-[minmax(0,1fr)_64px] gap-3 text-xs">
                <span className="min-w-0 truncate text-slate-300">{candidate.capability?.id || "-"}</span>
                <span className="text-right text-violet-200">{typeof candidate.score === "number" ? candidate.score.toFixed(2) : "-"}</span>
              </div>
            ))}
          </div>
        </div>
      ) : null}
      {composition?.steps?.length ? (
        <div className="mt-3 space-y-2">
          {composition.steps.slice(0, 6).map((step, index) => (
            <div key={`${step.stage}-${index}`} className="rounded-md border border-slate-800 bg-slate-950/45 px-3 py-2">
              <div className="text-xs font-medium text-slate-300">{step.stage || `step_${index + 1}`}</div>
              <div className="mt-1 text-xs text-slate-500">{step.action}</div>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function MissionRouterBlock({ item }: { item: EvidenceEntry }) {
  const slots = slotEntries(item.intent?.slots);
  return (
    <div className="rounded-lg border border-cyan-500/20 bg-cyan-400/[0.06] p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium text-cyan-50">Mission Router</span>
        {item.intent?.task_type ? <span className="rounded bg-cyan-400/10 px-2 py-0.5 text-xs text-cyan-200">{item.intent.task_type}</span> : null}
        {typeof item.intent?.confidence === "number" ? (
          <span className="rounded bg-slate-800 px-2 py-0.5 text-xs text-slate-400">confidence {item.intent.confidence.toFixed(2)}</span>
        ) : null}
      </div>
      <div className="grid gap-3 xl:grid-cols-2">
        <div className="rounded-md border border-slate-800 bg-slate-950/45 p-3">
          <div className="mb-2 text-xs font-medium text-slate-300">抽取槽位</div>
          {slots.length === 0 ? <div className="text-xs text-slate-500">未记录槽位</div> : null}
          <div className="space-y-1">
            {slots.map(([key, value]) => (
              <div key={key} className="grid grid-cols-[120px_minmax(0,1fr)] gap-2 text-xs">
                <span className="text-slate-500">{key}</span>
                <span className="min-w-0 break-words text-slate-300">{value}</span>
              </div>
            ))}
          </div>
        </div>
        <div className="rounded-md border border-slate-800 bg-slate-950/45 p-3">
          <div className="mb-2 text-xs font-medium text-slate-300">路由决策</div>
          <div className="space-y-1 text-xs">
            <div className="grid grid-cols-[120px_minmax(0,1fr)] gap-2">
              <span className="text-slate-500">workflow</span>
              <span className="break-words text-slate-300">{item.route?.workflow_id || "-"}</span>
            </div>
            <div className="grid grid-cols-[120px_minmax(0,1fr)] gap-2">
              <span className="text-slate-500">tool</span>
              <span className="break-words text-slate-300">{item.route?.tool_id || "-"}</span>
            </div>
            <div className="grid grid-cols-[120px_minmax(0,1fr)] gap-2">
              <span className="text-slate-500">why</span>
              <span className="break-words text-slate-300">{item.route?.reason || "-"}</span>
            </div>
            <div className="grid grid-cols-[120px_minmax(0,1fr)] gap-2">
              <span className="text-slate-500">clarify</span>
              <span className="break-words text-slate-300">
                {item.clarification?.should_ask ? item.clarification.question || item.clarification.reason || "需要澄清" : "无需澄清"}
              </span>
            </div>
          </div>
        </div>
      </div>
      {item.intent?.reasoning?.length ? (
        <div className="mt-3 flex flex-wrap gap-1">
          {item.intent.reasoning.map((reason) => (
            <span key={reason} className="rounded bg-slate-800 px-2 py-0.5 text-[11px] text-slate-400">
              {reason}
            </span>
          ))}
        </div>
      ) : null}
      {item.parser ? (
        <div className="mt-3 grid gap-2 rounded-md border border-slate-800 bg-slate-950/45 p-3 text-xs md:grid-cols-3">
          <div>
            <span className="text-slate-500">parser</span>
            <div className="mt-1 text-slate-300">{item.parser.decision || "-"}</div>
          </div>
          <div>
            <span className="text-slate-500">rule</span>
            <div className="mt-1 text-slate-300">{item.parser.rule?.task_type || "-"}</div>
          </div>
          <div>
            <span className="text-slate-500">llm</span>
            <div className="mt-1 text-slate-300">{item.parser.llm?.status || "disabled"}</div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function PlanPreviewBlock({ item }: { item: EvidenceEntry }) {
  const plan = item.plan_preview;
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/45 p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium text-slate-200">Plan Preview</span>
        <span className="rounded bg-slate-800 px-2 py-0.5 text-xs text-slate-400">risk {plan?.risk_level || "-"}</span>
        <span className={cn("rounded px-2 py-0.5 text-xs", plan?.requires_confirmation ? "bg-amber-400/10 text-amber-300" : "bg-emerald-400/10 text-emerald-300")}>
          {plan?.requires_confirmation ? "needs confirm" : "auto"}
        </span>
      </div>
      <p className="text-sm leading-6 text-slate-400">{plan?.summary || "-"}</p>
      <div className="mt-3 grid gap-3 md:grid-cols-3">
        <InfoMini title="Apps" items={plan?.apps ?? []} />
        <InfoMini title="Files" items={plan?.files ?? []} />
        <InfoMini title="Recipients" items={plan?.recipients ?? []} />
      </div>
      <div className="mt-3 space-y-2">
        {(plan?.steps ?? []).map((step, index) => (
          <div key={`${step.stage}-${index}`} className="rounded-md border border-slate-800 bg-slate-950/45 px-3 py-2">
            <div className="text-xs font-medium text-slate-300">{step.stage || `step_${index + 1}`}</div>
            <div className="mt-1 text-xs text-slate-500">{step.action}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function RuntimeBlock({ item }: { item: EvidenceEntry }) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/45 p-4">
      <div className="mb-3 text-sm font-medium text-slate-200">Runtime / Self-correction</div>
      <div className="grid gap-3 text-xs md:grid-cols-4">
        <div>
          <div className="text-slate-500">耗时</div>
          <div className="mt-1 text-slate-300">{msText(item.metrics?.duration_ms)}</div>
        </div>
        <div>
          <div className="text-slate-500">尝试次数</div>
          <div className="mt-1 text-slate-300">{item.metrics?.attempt_count ?? item.attempts?.length ?? "-"}</div>
        </div>
        <div>
          <div className="text-slate-500">失败分类</div>
          <div className="mt-1 text-slate-300">{item.metrics?.failure_class || "-"}</div>
        </div>
        <div>
          <div className="text-slate-500">重试策略</div>
          <div className="mt-1 text-slate-300">{item.retry?.should_retry ? `retry: ${item.retry.reason}` : item.retry?.reason || "-"}</div>
        </div>
      </div>
      {item.attempts?.length ? (
        <div className="mt-3 space-y-2">
          {item.attempts.map((attempt, index) => (
            <div key={index} className="rounded-md border border-slate-800 bg-slate-950/45 px-3 py-2 text-xs">
              <div className="flex items-center justify-between gap-3">
                <span className="text-slate-300">attempt {attempt.attempt ?? index + 1}</span>
                <span className={cn("rounded px-2 py-0.5", attempt.ok ? "bg-emerald-400/10 text-emerald-300" : "bg-amber-400/10 text-amber-300")}>{attempt.ok ? "OK" : "CHECK"}</span>
              </div>
              <div className="mt-1 text-slate-500">{attempt.failure_class || attempt.detail || "-"}</div>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function InfoMini({ title, items }: { title: string; items: string[] }) {
  return (
    <div className="rounded-md border border-slate-800 bg-slate-950/45 p-3">
      <div className="mb-2 text-xs text-slate-500">{title}</div>
      <div className="space-y-1">
        {items.length === 0 ? <div className="text-xs text-slate-600">-</div> : null}
        {items.slice(0, 4).map((item) => (
          <div key={item} className="truncate text-xs text-slate-400" title={item}>
            {item}
          </div>
        ))}
      </div>
    </div>
  );
}

function InfoBlock({ title, items, empty }: { title: string; items: string[]; empty: string }) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/45 p-4">
      <div className="mb-3 text-sm font-medium text-slate-200">{title}</div>
      {items.length === 0 ? <div className="text-sm text-slate-500">{empty}</div> : null}
      <div className="flex flex-wrap gap-2">
        {items.map((item) => (
          <span key={item} className="rounded-md bg-slate-800 px-2 py-1 text-xs text-slate-300">
            {item}
          </span>
        ))}
      </div>
    </div>
  );
}

function ScreenshotStrip({ paths }: { paths: string[] }) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/45 p-4">
      <div className="mb-3 text-sm font-medium text-slate-200">截图缩略图</div>
      {paths.length === 0 ? <div className="text-sm text-slate-500">暂无截图</div> : null}
      <div className="grid gap-3 md:grid-cols-3">
        {paths.slice(0, 6).map((path) => (
          <button
            key={path}
            type="button"
            onClick={() => void openPath(path)}
            className="overflow-hidden rounded-md border border-slate-800 bg-slate-950 text-left transition hover:border-cyan-500/30"
            title={path}
          >
            <img src={imageSrc(path)} alt="" className="h-32 w-full object-cover" />
            <div className="truncate px-2 py-1 text-xs text-slate-500">{shortPath(path)}</div>
          </button>
        ))}
      </div>
    </div>
  );
}

function Timeline({ rows }: { rows: EvidenceEntry["timeline"] }) {
  const [openKeys, setOpenKeys] = useState<Record<string, boolean>>({});
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/45 p-4">
      <div className="mb-3 text-sm font-medium text-slate-200">执行时间线</div>
      {rows.length === 0 ? <div className="text-sm text-slate-500">暂无步骤记录</div> : null}
      <div className="space-y-2">
        {rows.map((row, index) => {
          const key = `${row.ts}-${row.stage}-${index}`;
          const hasEvidence = row.screenshots.length > 0 || row.files.length > 0 || row.ocr_preview || row.checks.length > 0;
          return (
            <div key={key} className="grid grid-cols-[22px_minmax(0,1fr)] gap-3">
              <span
                className={cn(
                  "mt-1 h-2.5 w-2.5 rounded-full",
                  row.status === "done" && "bg-emerald-300",
                  row.status === "failed" && "bg-rose-300",
                  row.status === "running" && "bg-cyan-300",
                  row.status !== "done" && row.status !== "failed" && row.status !== "running" && "bg-amber-300",
                )}
              />
              <div className="min-w-0 rounded-md border border-slate-800 bg-slate-950/45 px-3 py-2">
                <button
                  type="button"
                  onClick={() => hasEvidence && setOpenKeys((prev) => ({ ...prev, [key]: !prev[key] }))}
                  className="flex w-full flex-wrap items-center gap-2 text-left"
                >
                  <span className="text-xs font-medium text-slate-200">{row.stage}</span>
                  <span className="rounded bg-slate-800 px-2 py-0.5 text-[11px] text-slate-400">{row.status}</span>
                  <span className="text-[11px] text-slate-600">{row.ts}</span>
                  {hasEvidence ? <span className="ml-auto text-[11px] text-cyan-300">{openKeys[key] ? "收起" : "展开证据"}</span> : null}
                </button>
                {row.detail ? <div className="mt-1 text-xs text-slate-500">{row.detail}</div> : null}
                {openKeys[key] && hasEvidence ? <TimelineEvidence row={row} /> : null}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function TimelineEvidence({ row }: { row: EvidenceEntry["timeline"][number] }) {
  return (
    <div className="mt-3 space-y-3 border-t border-slate-800 pt-3">
      {row.screenshots.length > 0 ? (
        <div className="grid gap-2 sm:grid-cols-2">
          {row.screenshots.slice(0, 4).map((path) => (
            <button
              key={path}
              type="button"
              onClick={() => void openPath(path)}
              className="overflow-hidden rounded-md border border-slate-800 bg-slate-900 text-left"
              title={path}
            >
              <img src={imageSrc(path)} alt="" className="h-28 w-full object-cover" />
              <div className="truncate px-2 py-1 text-[11px] text-slate-500">{shortPath(path)}</div>
            </button>
          ))}
        </div>
      ) : null}
      {row.checks.length > 0 ? (
        <div className="flex flex-wrap gap-1">
          {row.checks.map((check) => (
            <span key={check} className="rounded bg-slate-800 px-2 py-0.5 text-[11px] text-slate-400">
              {check}
            </span>
          ))}
        </div>
      ) : null}
      {row.ocr_preview ? (
        <pre className="max-h-36 overflow-auto whitespace-pre-wrap rounded bg-slate-900 p-2 text-[11px] leading-5 text-slate-500">{row.ocr_preview}</pre>
      ) : null}
      {row.files.length > 0 ? (
        <div className="space-y-1">
          {row.files.map((path) => (
            <button
              key={path}
              type="button"
              onClick={() => void openPath(path)}
              className="block w-full truncate rounded border border-slate-800 bg-slate-900 px-2 py-1 text-left text-[11px] text-slate-500 hover:text-cyan-100"
              title={path}
            >
              {shortPath(path)}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function PathList({ title, paths }: { title: string; paths: string[] }) {
  return (
    <div className="min-h-0 rounded-lg border border-slate-800 bg-slate-900/45 p-4">
      <div className="mb-3 text-sm font-medium text-slate-200">{title}</div>
      {paths.length === 0 ? <div className="text-sm text-slate-500">暂无记录</div> : null}
      <div className="max-h-64 space-y-2 overflow-auto pr-1">
        {paths.map((path) => (
          <button
            key={path}
            type="button"
            onClick={() => void openPath(path)}
            className="flex w-full items-center justify-between gap-3 rounded border border-slate-800 bg-slate-950/50 px-3 py-2 text-left text-xs text-slate-400 transition hover:border-cyan-500/25 hover:text-cyan-100"
            title={path}
          >
            <span className="min-w-0 truncate">{shortPath(path)}</span>
            <ExternalLink className="h-3.5 w-3.5 shrink-0" />
          </button>
        ))}
      </div>
    </div>
  );
}
