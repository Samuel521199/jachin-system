import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  BrainCircuit,
  Clock3,
  Database,
  FileText,
  GitBranch,
  ListChecks,
  Loader2,
  Network,
  RefreshCw,
  Repeat2,
  ShieldAlert,
  Sparkles,
} from "lucide-react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { getL3SkillsBaseUrl } from "../../lib/api";

type MemoryGrowthCounts = {
  raw_events: number;
  concepts: number;
  playbooks: number;
  outputs: number;
  conflicts: number;
  graph_nodes: number;
  graph_edges: number;
};

type MemoryGrowthLatest = {
  pipeline_report: string;
  weekly_report: string;
  graph_event: string;
  connector_index: string;
  artifact_curator_report?: string;
};

type TrendRow = {
  date: string;
  raw_events: number;
  concepts: number;
  playbooks: number;
  outputs: number;
  conflicts: number;
};

type RankedRow = {
  reason?: string;
  pattern?: string;
  count: number;
  latest_path?: string;
  latest_date?: string;
  examples?: string[];
};

type QueueRow = {
  kind: string;
  source: string;
  reason: string;
  summary: string;
  path: string;
  date: string;
};

type StaleConceptRow = {
  summary: string;
  reason: string;
  date: string;
  path: string;
};

type GovernanceHistoryRow = {
  governance_id: string;
  action: string;
  created_at: string;
  note: string;
  summary: string;
  item_path: string;
  item_pattern: string;
  side_effect_count: number;
  report_path: string;
};

type GovernanceRecommendationRow = {
  id: string;
  priority: "high" | "medium" | "low" | string;
  priority_score?: number;
  title: string;
  reason: string;
  action: GovernanceAction;
  item: Record<string, unknown>;
  source: string;
  strategy?: {
    weight: number;
    execution_mode: "normal" | "batch_ok" | "manual_review" | string;
    requires_more_evidence: boolean;
    reason: string;
    global_mode: string;
    trend_delta: number;
  };
};

type GovernanceEffectiveness = {
  score: number;
  grade: "healthy" | "watch" | "weak" | "no_data" | string;
  action_count: number;
  success_count: number;
  failure_count: number;
  success_rate: number;
  confirmed_concept_count: number;
  generated_playbook_count: number;
  revalidated_count: number;
  archived_count: number;
  post_governance_failure_count: number;
  open_conflict_pressure: number;
  open_failure_pressure: number;
  signals: string[];
  recommendations: string[];
};

type GovernanceEffectivenessTrendRow = {
  date: string;
  week_id: string;
  score: number;
  action_count: number;
  success_count: number;
  failure_count: number;
  conflict_pressure: number;
  failure_pressure: number;
};

type GovernanceActionAttributionRow = {
  action: string;
  count?: number;
  success_count?: number;
  failure_count?: number;
  paths?: string[];
  path?: string;
  failed_count?: number;
};

type GovernanceEffectivenessAttribution = {
  effective_actions: GovernanceActionAttributionRow[];
  ineffective_actions: GovernanceActionAttributionRow[];
  repeated_failures: GovernanceActionAttributionRow[];
  latest?: Record<string, unknown>;
};

type GovernanceStrategyPolicy = {
  latest_score: number;
  trend_delta: number;
  global_mode: "normal" | "accelerate" | "cautious" | string;
  action_policy: Record<string, Record<string, unknown>>;
};

type ArtifactUsageTrendRow = {
  date: string;
  week_id: string;
  artifact_count: number;
  active_artifact_count: number;
  total_use_count: number;
  success_count: number;
  failure_count: number;
  success_rate: number;
  low_success_count: number;
  high_failure_count: number;
  stale_unused_count: number;
};

type ArtifactUsageRow = {
  path: string;
  id?: string;
  type?: string;
  summary?: string;
  memory_use_count: number;
  memory_success_count: number;
  memory_failure_count: number;
  memory_success_rate: number;
  memory_last_used_at?: string;
  memory_last_failure_reason?: string;
  recommendation?: string;
  reason?: string;
};

type ArtifactUsageAttribution = {
  best_playbooks: ArtifactUsageRow[];
  top_successful_assets: ArtifactUsageRow[];
  low_success_assets: ArtifactUsageRow[];
  high_failure_assets: ArtifactUsageRow[];
  stale_unused_assets: ArtifactUsageRow[];
  latest?: Record<string, unknown>;
};

type MemoryGrowthMonitoring = {
  trends: {
    days_7: TrendRow[];
    days_14: TrendRow[];
    days_30: TrendRow[];
  };
  conflict_types: RankedRow[];
  stale_concepts: StaleConceptRow[];
  failure_patterns: RankedRow[];
  pending_confirmation_queue: QueueRow[];
  governance_history: GovernanceHistoryRow[];
  artifact_usage?: ArtifactUsageRow[];
  artifact_usage_trends?: {
    days_7: ArtifactUsageTrendRow[];
    days_14: ArtifactUsageTrendRow[];
    days_30: ArtifactUsageTrendRow[];
  };
  artifact_usage_attribution?: ArtifactUsageAttribution;
  artifact_usage_recommendations?: Record<string, unknown>[];
  governance_recommendations: GovernanceRecommendationRow[];
  governance_effectiveness?: GovernanceEffectiveness;
  governance_effectiveness_trends?: {
    days_7: GovernanceEffectivenessTrendRow[];
    days_14: GovernanceEffectivenessTrendRow[];
    days_30: GovernanceEffectivenessTrendRow[];
  };
  governance_effectiveness_attribution?: GovernanceEffectivenessAttribution;
  governance_strategy_policy?: GovernanceStrategyPolicy;
  health: {
    quality_score: number;
    risk_level: "low" | "medium" | "high" | string;
    stale_concept_count: number;
    pending_confirmation_count: number;
    failure_pattern_count: number;
    governance_history_count?: number;
    artifact_usage_count?: number;
    artifact_low_success_count?: number;
    artifact_stale_unused_count?: number;
    recommendation_count?: number;
    governance_effectiveness_score?: number;
  };
};

type MemoryGrowthStatus = {
  ok: boolean;
  root: string;
  counts: MemoryGrowthCounts;
  monitoring?: MemoryGrowthMonitoring;
  latest: MemoryGrowthLatest;
  available_actions: string[];
  error?: string;
};

type ActionKey = "pipeline" | "weekly-review" | "graph-sync" | "connector-sync" | "artifact-curator";
type GovernanceAction =
  | "confirm_pending"
  | "reject_pending"
  | "defer_pending"
  | "revalidate_stale"
  | "archive_stale"
  | "generate_failure_playbook"
  | "rewrite_or_downrank"
  | "create_or_update_recovery_playbook"
  | "archive_or_revalidate"
  | "promote_preferred_guidance"
  | "revalidate_artifact"
  | "merge_artifact_draft";

const actionLabels: Record<ActionKey, { title: string; subtitle: string; icon: typeof Sparkles; payload?: Record<string, unknown> }> = {
  pipeline: {
    title: "运行消化管线",
    subtitle: "Raw Evidence -> Concepts / Playbooks / Outputs",
    icon: Sparkles,
    payload: {
      promote_concepts: true,
      build_playbooks: true,
      review_outputs: true,
      sync_graph: true,
      graph_connector_ids: ["local_json_graph"],
    },
  },
  "weekly-review": {
    title: "周复盘治理",
    subtitle: "检查陈旧概念、冲突、生命周期和失败模式",
    icon: Repeat2,
    payload: { stale_after_days: 30 },
  },
  "graph-sync": {
    title: "同步本地图谱",
    subtitle: "Markdown Wiki -> Graph nodes / edges",
    icon: GitBranch,
  },
  "connector-sync": {
    title: "同步图谱连接器",
    subtitle: "LocalJson / Cognee / Graphiti connector status",
    icon: Network,
    payload: { graph_connector_ids: ["local_json_graph", "cognee", "graphiti"] },
  },
  "artifact-curator": {
    title: "运行 Artifact Curator",
    subtitle: "Rewrite requests -> reviewable drafts / confirmation queue",
    icon: BrainCircuit,
    payload: { max_items: 10 },
  },
};

function numberText(value: number | undefined) {
  return Number(value || 0).toLocaleString();
}

function dayLabel(value: string) {
  return value ? value.slice(5) : "";
}

function compactPath(path?: string) {
  if (!path) return "暂无";
  const parts = path.replace(/\\/g, "/").split("/");
  return parts.length > 3 ? parts.slice(-3).join("/") : path;
}

function StatCard({
  label,
  value,
  Icon,
  tone = "cyan",
}: {
  label: string;
  value: number | string;
  Icon: typeof Database;
  tone?: "cyan" | "emerald" | "amber" | "violet" | "rose";
}) {
  const toneClass =
    tone === "emerald"
      ? "border-emerald-400/25 bg-emerald-500/10 text-emerald-200"
      : tone === "amber"
        ? "border-amber-400/25 bg-amber-500/10 text-amber-200"
        : tone === "violet"
          ? "border-violet-400/25 bg-violet-500/10 text-violet-200"
          : tone === "rose"
            ? "border-rose-400/25 bg-rose-500/10 text-rose-200"
            : "border-cyan-400/25 bg-cyan-500/10 text-cyan-200";
  return (
    <div className="rounded-lg border border-cyan-400/15 bg-slate-950/55 p-4">
      <div className="flex items-center justify-between gap-3">
        <span className="text-xs text-slate-400">{label}</span>
        <span className={`flex h-8 w-8 items-center justify-center rounded-md border ${toneClass}`}>
          <Icon className="h-4 w-4" />
        </span>
      </div>
      <div className="mt-3 text-3xl font-semibold text-white">{value}</div>
    </div>
  );
}

async function callMemoryGrowth<T>(path: string, init?: RequestInit): Promise<T> {
  const base = await getL3SkillsBaseUrl();
  const response = await fetch(`${base}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...(init?.headers ?? {}),
    },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || payload?.ok === false) {
    throw new Error(payload?.error || `HTTP ${response.status}`);
  }
  return payload as T;
}

function LatestPath({ label, path }: { label: string; path?: string }) {
  return (
    <div className="rounded-md border border-white/10 bg-white/[0.03] p-3">
      <div className="text-xs text-slate-500">{label}</div>
      <div className="mt-1 break-all font-mono text-xs text-cyan-100/90">{path || "暂无"}</div>
    </div>
  );
}

export function MemoryGrowthPanel() {
  const [status, setStatus] = useState<MemoryGrowthStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyAction, setBusyAction] = useState<ActionKey | null>(null);
  const [busyGovernance, setBusyGovernance] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [trendDays, setTrendDays] = useState<7 | 14 | 30>(14);

  const load = useCallback(async (bypassCache = false) => {
    setLoading(true);
    try {
      if (bypassCache) {
        await getL3SkillsBaseUrl({ bypassCache: true });
      }
      const next = await callMemoryGrowth<MemoryGrowthStatus>("/api/v1/memory-growth/status");
      setStatus(next);
      setNotice(null);
    } catch (e) {
      setNotice(`Memory Growth 状态加载失败：${String(e)}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const counts = status?.counts;
  const monitoring = status?.monitoring;
  const trendRows = monitoring?.trends?.[`days_${trendDays}`] ?? [];
  const health = monitoring?.health;
  const qualityScore = useMemo(() => {
    if (typeof health?.quality_score === "number") return health.quality_score;
    if (!counts) return 0;
    const durable = counts.concepts + counts.playbooks + counts.outputs;
    const risk = counts.conflicts;
    if (durable <= 0) return 0;
    return Math.max(0, Math.min(100, Math.round((durable / Math.max(1, durable + risk)) * 100)));
  }, [counts, health?.quality_score]);

  const runAction = async (action: ActionKey) => {
    setBusyAction(action);
    setNotice(null);
    try {
      const config = actionLabels[action];
      await callMemoryGrowth(`/api/v1/memory-growth/${action}`, {
        method: "POST",
        body: JSON.stringify(config.payload ?? {}),
      });
      const next = await callMemoryGrowth<MemoryGrowthStatus>("/api/v1/memory-growth/status");
      setStatus(next);
      setNotice(`${config.title} 已完成，统计和报告路径已刷新。`);
    } catch (e) {
      setNotice(`${actionLabels[action].title} 失败：${String(e)}`);
    } finally {
      setBusyAction(null);
    }
  };

  const runGovernance = async (action: GovernanceAction, item: Record<string, unknown>, label: string) => {
    const busyKey = `${action}:${String(item.path || item.pattern || item.reason || label)}`;
    setBusyGovernance(busyKey);
    setNotice(null);
    try {
      await callMemoryGrowth("/api/v1/memory-growth/governance", {
        method: "POST",
        body: JSON.stringify({ action, item, note: label }),
      });
      const next = await callMemoryGrowth<MemoryGrowthStatus>("/api/v1/memory-growth/status");
      setStatus(next);
      setNotice(`${label} 已完成，治理证据已写入 Memory Growth。`);
    } catch (e) {
      setNotice(`${label} 失败：${String(e)}`);
    } finally {
      setBusyGovernance(null);
    }
  };

  const runBatchGovernance = async (rows: GovernanceRecommendationRow[], label: string) => {
    const selected = rows
      .filter((row) => row.strategy?.execution_mode !== "manual_review" && row.strategy?.requires_more_evidence !== true)
      .slice(0, 3);
    if (!selected.length) {
      setNotice("当前治理建议需要人工审查，未执行批量治理。");
      return;
    }
    setBusyGovernance(`batch:${label}`);
    setNotice(null);
    try {
      await callMemoryGrowth("/api/v1/memory-growth/batch-governance", {
        method: "POST",
        body: JSON.stringify({
          note: label,
          operations: selected.map((row) => ({ action: row.action, item: row.item, note: row.title })),
        }),
      });
      const next = await callMemoryGrowth<MemoryGrowthStatus>("/api/v1/memory-growth/status");
      setStatus(next);
      setNotice(`${label} 已完成，批量治理证据已写入 Memory Growth。`);
    } catch (e) {
      setNotice(`${label} 失败：${String(e)}`);
    } finally {
      setBusyGovernance(null);
    }
  };

  return (
    <div className="console-fiber-host console-holo-slab flex h-full min-h-0 flex-col overflow-hidden p-4">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-cyan-400/15 pb-4">
        <div>
          <p className="text-xs uppercase tracking-[0.32em] text-cyan-300/70">AI Self-Growing Knowledge</p>
          <h1 className="mt-1 text-2xl font-semibold text-cyan-50">Memory Growth 控制台</h1>
          <p className="mt-1 text-sm text-slate-400">
            原始证据、时间记忆、Markdown Wiki、方法论沉淀和图谱同步的统一入口。
          </p>
        </div>
        <button
          className="inline-flex h-10 items-center gap-2 rounded-md border border-cyan-400/25 bg-cyan-500/10 px-3 text-sm text-cyan-100 transition hover:bg-cyan-400/15 disabled:opacity-50"
          onClick={() => void load(true)}
          disabled={loading || Boolean(busyAction)}
        >
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
          刷新状态
        </button>
      </div>

      {notice ? <div className="mt-3 rounded-md border border-cyan-400/20 bg-cyan-500/10 px-3 py-2 text-sm text-cyan-100">{notice}</div> : null}

      <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-4 xl:grid-cols-7">
        <StatCard label="原始证据" value={numberText(counts?.raw_events)} Icon={Database} />
        <StatCard label="高价值概念" value={numberText(counts?.concepts)} Icon={BrainCircuit} tone="emerald" />
        <StatCard label="方法论" value={numberText(counts?.playbooks)} Icon={FileText} tone="violet" />
        <StatCard label="输出回流" value={numberText(counts?.outputs)} Icon={Activity} tone="cyan" />
        <StatCard label="冲突待审" value={numberText(counts?.conflicts)} Icon={Repeat2} tone={counts?.conflicts ? "rose" : "amber"} />
        <StatCard label="图谱节点" value={numberText(counts?.graph_nodes)} Icon={GitBranch} tone="emerald" />
        <StatCard label="图谱边" value={numberText(counts?.graph_edges)} Icon={Network} tone="violet" />
      </div>

      <div className="mt-4 grid min-h-0 flex-1 grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_22rem]">
        <section className="min-h-0 overflow-auto rounded-lg border border-cyan-400/15 bg-slate-950/55 p-4">
          <div className="flex items-center justify-between gap-3">
            <div>
              <h2 className="text-base font-semibold text-cyan-50">增长动作</h2>
              <p className="mt-1 text-xs text-slate-500">所有动作都会回写报告路径，后续 Evidence / Wiki 页面可以继续追踪。</p>
            </div>
            <span className="rounded-md border border-emerald-400/20 bg-emerald-500/10 px-2 py-1 text-xs text-emerald-200">
              质量分 {qualityScore}%
            </span>
          </div>

          <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-2">
            {(Object.keys(actionLabels) as ActionKey[]).map((action) => {
              const config = actionLabels[action];
              const Icon = config.icon;
              const running = busyAction === action;
              return (
                <button
                  key={action}
                  className="min-h-24 rounded-lg border border-cyan-400/15 bg-white/[0.03] p-4 text-left transition hover:border-cyan-300/35 hover:bg-cyan-400/5 disabled:opacity-50"
                  onClick={() => void runAction(action)}
                  disabled={loading || Boolean(busyAction)}
                >
                  <div className="flex items-center justify-between gap-3">
                    <span className="flex h-9 w-9 items-center justify-center rounded-md border border-cyan-300/20 bg-cyan-400/10 text-cyan-100">
                      {running ? <Loader2 className="h-4 w-4 animate-spin" /> : <Icon className="h-4 w-4" />}
                    </span>
                    <span className="font-mono text-[11px] text-slate-500">{action}</span>
                  </div>
                  <div className="mt-3 text-sm font-medium text-cyan-50">{config.title}</div>
                  <div className="mt-1 text-xs text-slate-500">{config.subtitle}</div>
                </button>
              );
            })}
          </div>

          <div className="mt-4 rounded-lg border border-white/10 bg-white/[0.03] p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="text-base font-semibold text-cyan-50">增长趋势</h2>
                <p className="mt-1 text-xs text-slate-500">观察原始证据、概念、方法论、输出和冲突是否在持续沉淀。</p>
              </div>
              <div className="flex rounded-md border border-cyan-400/20 bg-slate-950/70 p-1">
                {([7, 14, 30] as const).map((days) => (
                  <button
                    key={days}
                    className={`rounded px-2 py-1 text-xs transition ${
                      trendDays === days ? "bg-cyan-400/15 text-cyan-100" : "text-slate-500 hover:text-cyan-100"
                    }`}
                    onClick={() => setTrendDays(days)}
                  >
                    {days}天
                  </button>
                ))}
              </div>
            </div>
            <div className="mt-4 h-64">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={trendRows} margin={{ top: 8, right: 16, bottom: 4, left: -18 }}>
                  <CartesianGrid stroke="rgba(148, 163, 184, 0.16)" strokeDasharray="3 3" />
                  <XAxis dataKey="date" tickFormatter={dayLabel} tick={{ fill: "#94a3b8", fontSize: 11 }} axisLine={false} tickLine={false} />
                  <YAxis tick={{ fill: "#94a3b8", fontSize: 11 }} axisLine={false} tickLine={false} allowDecimals={false} />
                  <Tooltip
                    contentStyle={{
                      background: "rgba(2, 6, 23, 0.96)",
                      border: "1px solid rgba(34, 211, 238, 0.24)",
                      borderRadius: 8,
                      color: "#cffafe",
                    }}
                    labelFormatter={(label) => `日期 ${label}`}
                  />
                  <Line type="monotone" dataKey="raw_events" name="原始证据" stroke="#22d3ee" strokeWidth={2} dot={false} />
                  <Line type="monotone" dataKey="concepts" name="概念" stroke="#34d399" strokeWidth={2} dot={false} />
                  <Line type="monotone" dataKey="playbooks" name="方法论" stroke="#a78bfa" strokeWidth={2} dot={false} />
                  <Line type="monotone" dataKey="outputs" name="输出" stroke="#fbbf24" strokeWidth={2} dot={false} />
                  <Line type="monotone" dataKey="conflicts" name="冲突" stroke="#fb7185" strokeWidth={2} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>

          <div className="mt-4 grid grid-cols-1 gap-3 lg:grid-cols-2">
            <RankedList
              title="冲突类型"
              subtitle="需要治理的知识冲突分布"
              icon={ShieldAlert}
              rows={monitoring?.conflict_types ?? []}
              empty="暂无冲突"
              tone="rose"
            />
            <RankedList
              title="失败模式"
              subtitle="从失败 TurnClosure 和冲突中聚合"
              icon={AlertTriangle}
              rows={monitoring?.failure_patterns ?? []}
              empty="暂无失败模式"
              tone="amber"
              actionLabel="生成 Playbook"
              onAction={(row) => void runGovernance("generate_failure_playbook", row as unknown as Record<string, unknown>, "生成失败恢复方法论")}
              busyKey={busyGovernance}
            />
          </div>

          <div className="mt-4 rounded-lg border border-white/10 bg-white/[0.03] p-4">
            <div className="text-xs text-slate-500">Memory Growth Root</div>
            <div className="mt-1 break-all font-mono text-xs text-cyan-100/90">{status?.root || "等待 L3 返回"}</div>
          </div>
        </section>

        <section className="min-h-0 overflow-auto rounded-lg border border-cyan-400/15 bg-slate-950/55 p-4">
          <div className="mb-4 grid grid-cols-3 gap-2">
            <MiniHealthCard label="风险" value={health?.risk_level || "unknown"} tone={health?.risk_level === "high" ? "rose" : health?.risk_level === "medium" ? "amber" : "emerald"} />
            <MiniHealthCard label="陈旧" value={health?.stale_concept_count ?? 0} tone="amber" />
            <MiniHealthCard label="待确认" value={health?.pending_confirmation_count ?? 0} tone="cyan" />
          </div>
          <GovernanceEffectivenessCard
            data={monitoring?.governance_effectiveness}
            trends={monitoring?.governance_effectiveness_trends?.days_30 ?? []}
            attribution={monitoring?.governance_effectiveness_attribution}
            policy={monitoring?.governance_strategy_policy}
          />
          <ArtifactUsageCard
            rows={monitoring?.artifact_usage ?? []}
            trends={monitoring?.artifact_usage_trends?.days_30 ?? []}
            attribution={monitoring?.artifact_usage_attribution}
            recommendations={monitoring?.artifact_usage_recommendations ?? []}
            busyKey={busyGovernance}
            onRun={(action, item, label) => void runGovernance(action, item, label)}
          />
          <RecommendationList
            rows={monitoring?.governance_recommendations ?? []}
            busyKey={busyGovernance}
            onRun={(row) => void runGovernance(row.action, row.item, row.title)}
            onBatch={(rows) => void runBatchGovernance(rows, "批量执行治理建议")}
          />
          <GovernanceHistoryList rows={monitoring?.governance_history ?? []} />

          <h2 className="text-base font-semibold text-cyan-50">最新报告</h2>
          <p className="mt-1 text-xs text-slate-500">这里显示每个阶段最新产物，方便追溯和排查。</p>
          <div className="mt-4 space-y-3">
            <LatestPath label="Pipeline Report" path={status?.latest?.pipeline_report} />
            <LatestPath label="Weekly Review" path={status?.latest?.weekly_report} />
            <LatestPath label="Graph Event" path={status?.latest?.graph_event} />
            <LatestPath label="Connector Index" path={status?.latest?.connector_index} />
            <LatestPath label="Artifact Curator" path={status?.latest?.artifact_curator_report} />
          </div>
          <div className="mt-4 rounded-md border border-white/10 bg-white/[0.03] p-3">
            <div className="text-xs text-slate-500">可用动作</div>
            <div className="mt-2 flex flex-wrap gap-2">
              {(status?.available_actions ?? []).map((item) => (
                <span key={item} className="rounded border border-cyan-300/20 bg-cyan-400/10 px-2 py-1 font-mono text-[11px] text-cyan-100">
                  {item}
                </span>
              ))}
              {!status?.available_actions?.length ? <span className="text-xs text-slate-500">等待状态加载</span> : null}
            </div>
          </div>

          <QueueList
            title="待用户确认"
            subtitle="不能自动沉淀的偏好、事实或 pending decision"
            icon={ListChecks}
            rows={monitoring?.pending_confirmation_queue ?? []}
            empty="暂无待确认知识"
            onGovernance={(action, row, label) => void runGovernance(action, row as unknown as Record<string, unknown>, label)}
            busyKey={busyGovernance}
          />
          <StaleConceptList
            rows={monitoring?.stale_concepts ?? []}
            onGovernance={(action, row, label) => void runGovernance(action, row as unknown as Record<string, unknown>, label)}
            busyKey={busyGovernance}
          />
        </section>
      </div>
    </div>
  );
}

function GovernanceEffectivenessCard({
  data,
  trends,
  attribution,
  policy,
}: {
  data?: GovernanceEffectiveness;
  trends: GovernanceEffectivenessTrendRow[];
  attribution?: GovernanceEffectivenessAttribution;
  policy?: GovernanceStrategyPolicy;
}) {
  const score = data?.score ?? 0;
  const grade = data?.grade ?? "no_data";
  const tone =
    grade === "healthy"
      ? "border-emerald-300/25 bg-emerald-400/10 text-emerald-100"
      : grade === "watch"
        ? "border-amber-300/25 bg-amber-400/10 text-amber-100"
        : grade === "weak"
          ? "border-rose-300/25 bg-rose-400/10 text-rose-100"
          : "border-slate-300/20 bg-slate-400/10 text-slate-200";
  return (
    <div className="mb-4 rounded-lg border border-white/10 bg-white/[0.03] p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-cyan-50">治理效果评分</h2>
          <p className="mt-1 text-xs text-slate-500">根据治理成功率、确认沉淀、Playbook 产出、失败重现和冲突压力计算。</p>
        </div>
        <span className={`rounded border px-2 py-1 text-xs ${tone}`}>{grade}</span>
      </div>
      <div className="mt-3 flex items-end gap-3">
        <div className="text-3xl font-semibold text-cyan-50">{score}</div>
        <div className="pb-1 text-xs text-slate-500">/ 100</div>
      </div>
      <div className="mt-2 flex flex-wrap gap-2 text-[11px]">
        <span className="rounded border border-cyan-300/20 bg-cyan-400/10 px-2 py-1 text-cyan-100">
          strategy {policy?.global_mode ?? "normal"}
        </span>
        <span className="rounded border border-slate-300/15 bg-slate-400/10 px-2 py-1 text-slate-300">
          trend {Number(policy?.trend_delta ?? 0).toFixed(1)}
        </span>
      </div>
      <div className="mt-3 grid grid-cols-3 gap-2">
        <MiniHealthCard label="成功" value={data?.success_count ?? 0} tone="emerald" />
        <MiniHealthCard label="失败" value={data?.failure_count ?? 0} tone={(data?.failure_count ?? 0) ? "rose" : "cyan"} />
        <MiniHealthCard label="Playbook" value={data?.generated_playbook_count ?? 0} tone="amber" />
      </div>
      <div className="mt-3 space-y-1">
        {(data?.recommendations ?? ["暂无治理效果数据，先执行治理动作。"]).slice(0, 2).map((item, index) => (
          <div key={`${item}-${index}`} className="text-xs text-slate-400">
            {item}
          </div>
        ))}
      </div>
      <div className="mt-4 h-32 rounded-md border border-white/10 bg-slate-950/45 p-2">
        {trends.length ? (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={trends} margin={{ top: 8, right: 10, bottom: 2, left: -24 }}>
              <CartesianGrid stroke="rgba(148, 163, 184, 0.12)" strokeDasharray="3 3" />
              <XAxis dataKey="date" tickFormatter={dayLabel} tick={{ fill: "#94a3b8", fontSize: 10 }} axisLine={false} tickLine={false} />
              <YAxis domain={[0, 100]} tick={{ fill: "#94a3b8", fontSize: 10 }} axisLine={false} tickLine={false} />
              <Tooltip
                contentStyle={{
                  background: "rgba(2, 6, 23, 0.96)",
                  border: "1px solid rgba(34, 211, 238, 0.24)",
                  borderRadius: 8,
                  color: "#cffafe",
                }}
              />
              <Line type="monotone" dataKey="score" name="治理评分" stroke="#22d3ee" strokeWidth={2} dot={{ r: 2 }} />
              <Line type="monotone" dataKey="conflict_pressure" name="冲突压力" stroke="#fb7185" strokeWidth={1.5} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        ) : (
          <div className="flex h-full items-center justify-center text-xs text-slate-500">等待 Weekly Review 生成治理趋势</div>
        )}
      </div>
      <div className="mt-3 grid grid-cols-1 gap-2">
        <AttributionList title="最有效动作" rows={attribution?.effective_actions ?? []} empty="暂无有效动作归因" tone="emerald" />
        <AttributionList title="反复失败动作" rows={attribution?.ineffective_actions ?? attribution?.repeated_failures ?? []} empty="暂无反复失败动作" tone="rose" />
      </div>
    </div>
  );
}

function AttributionList({
  title,
  rows,
  empty,
  tone,
}: {
  title: string;
  rows: GovernanceActionAttributionRow[];
  empty: string;
  tone: "emerald" | "rose";
}) {
  const toneClass = tone === "emerald" ? "text-emerald-200" : "text-rose-200";
  return (
    <div className="rounded-md border border-white/10 bg-slate-950/45 p-2">
      <div className={`text-xs font-semibold ${toneClass}`}>{title}</div>
      <div className="mt-2 space-y-1">
        {rows.slice(0, 3).map((row, index) => (
          <div key={`${row.action}-${row.path || index}`} className="flex items-center justify-between gap-2 text-[11px]">
            <span className="truncate font-mono text-cyan-100">{row.action}</span>
            <span className="text-slate-500">成功 {row.success_count ?? 0} / 失败 {row.failure_count ?? row.failed_count ?? 0}</span>
          </div>
        ))}
        {rows.length === 0 ? <div className="text-[11px] text-slate-500">{empty}</div> : null}
      </div>
    </div>
  );
}

function ArtifactUsageCard({
  rows,
  trends,
  attribution,
  recommendations,
  busyKey,
  onRun,
}: {
  rows: ArtifactUsageRow[];
  trends: ArtifactUsageTrendRow[];
  attribution?: ArtifactUsageAttribution;
  recommendations: Record<string, unknown>[];
  busyKey?: string | null;
  onRun: (action: GovernanceAction, item: Record<string, unknown>, label: string) => void;
}) {
  const latest = trends.length ? trends[trends.length - 1] : undefined;
  return (
    <div className="mb-4 rounded-lg border border-white/10 bg-white/[0.03] p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-cyan-50">Artifact Learning</h2>
          <p className="mt-1 text-xs text-slate-500">Tracks which concepts/playbooks are actually used and whether they help tasks succeed.</p>
        </div>
        <span className="rounded border border-cyan-300/20 bg-cyan-400/10 px-2 py-1 text-xs text-cyan-100">
          {rows.length} assets
        </span>
      </div>
      <div className="mt-3 grid grid-cols-3 gap-2">
        <MiniHealthCard label="Uses" value={latest?.total_use_count ?? rows.reduce((sum, row) => sum + Number(row.memory_use_count || 0), 0)} tone="cyan" />
        <MiniHealthCard label="Success" value={`${Math.round((latest?.success_rate ?? 0) * 100)}%`} tone="emerald" />
        <MiniHealthCard label="Low" value={latest?.low_success_count ?? attribution?.low_success_assets?.length ?? 0} tone={(latest?.low_success_count ?? 0) ? "rose" : "cyan"} />
      </div>
      <div className="mt-4 h-32 rounded-md border border-white/10 bg-slate-950/45 p-2">
        {trends.length ? (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={trends} margin={{ top: 8, right: 10, bottom: 2, left: -24 }}>
              <CartesianGrid stroke="rgba(148, 163, 184, 0.12)" strokeDasharray="3 3" />
              <XAxis dataKey="date" tickFormatter={dayLabel} tick={{ fill: "#94a3b8", fontSize: 10 }} axisLine={false} tickLine={false} />
              <YAxis tick={{ fill: "#94a3b8", fontSize: 10 }} axisLine={false} tickLine={false} />
              <Tooltip
                contentStyle={{
                  background: "rgba(2, 6, 23, 0.96)",
                  border: "1px solid rgba(34, 211, 238, 0.24)",
                  borderRadius: 8,
                  color: "#cffafe",
                }}
              />
              <Line type="monotone" dataKey="total_use_count" name="uses" stroke="#22d3ee" strokeWidth={2} dot={{ r: 2 }} />
              <Line type="monotone" dataKey="failure_count" name="failures" stroke="#fb7185" strokeWidth={1.5} dot={false} />
              <Line type="monotone" dataKey="low_success_count" name="low-success" stroke="#fbbf24" strokeWidth={1.5} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        ) : (
          <div className="flex h-full items-center justify-center text-xs text-slate-500">Waiting for Weekly Review artifact usage trends</div>
        )}
      </div>
      <div className="mt-3 grid grid-cols-1 gap-2">
        <ArtifactMiniList title="Best playbooks" rows={attribution?.best_playbooks ?? []} empty="No successful playbook yet" tone="emerald" />
        <ArtifactMiniList title="Needs rewrite" rows={attribution?.low_success_assets ?? attribution?.high_failure_assets ?? []} empty="No low-success asset" tone="rose" />
      </div>
      {recommendations.length ? (
        <div className="mt-3 rounded-md border border-amber-300/15 bg-amber-400/5 p-2">
          <div className="text-xs font-semibold text-amber-100">Recommended governance</div>
          <div className="mt-2 space-y-1">
            {recommendations.slice(0, 3).map((row, index) => (
              <div key={`${row.action}-${row.target}-${index}`} className="flex items-center justify-between gap-2 text-[11px] text-slate-400">
                <div className="min-w-0">
                  <span className="font-mono text-amber-100">{String(row.action || "review")}</span>
                  <span className="ml-2 break-all">{String(row.target || "")}</span>
                </div>
                <button
                  className="flex-shrink-0 rounded border border-amber-300/25 bg-amber-400/10 px-2 py-1 text-[11px] text-amber-100 hover:bg-amber-400/15 disabled:opacity-50"
                  disabled={Boolean(busyKey)}
                  onClick={() =>
                    onRun(
                      String(row.action || "revalidate_artifact") as GovernanceAction,
                      { target: row.target, reason: row.reason, priority: row.priority },
                      `Artifact governance: ${String(row.action || "review")}`,
                    )
                  }
                >
                  {busyKey ? "Running" : "Run"}
                </button>
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function ArtifactMiniList({
  title,
  rows,
  empty,
  tone,
}: {
  title: string;
  rows: ArtifactUsageRow[];
  empty: string;
  tone: "emerald" | "rose";
}) {
  const toneClass = tone === "emerald" ? "text-emerald-200" : "text-rose-200";
  return (
    <div className="rounded-md border border-white/10 bg-slate-950/45 p-2">
      <div className={`text-xs font-semibold ${toneClass}`}>{title}</div>
      <div className="mt-2 space-y-1">
        {rows.slice(0, 3).map((row) => (
          <div key={row.path} className="text-[11px]">
            <div className="truncate font-mono text-cyan-100">{row.path}</div>
            <div className="text-slate-500">
              uses {row.memory_use_count ?? 0} · success {Math.round(Number(row.memory_success_rate || 0) * 100)}% · failures {row.memory_failure_count ?? 0}
            </div>
          </div>
        ))}
        {rows.length === 0 ? <div className="text-[11px] text-slate-500">{empty}</div> : null}
      </div>
    </div>
  );
}

function RecommendationList({
  rows,
  busyKey,
  onRun,
  onBatch,
}: {
  rows: GovernanceRecommendationRow[];
  busyKey?: string | null;
  onRun: (row: GovernanceRecommendationRow) => void;
  onBatch?: (rows: GovernanceRecommendationRow[]) => void;
}) {
  const batchableCount = rows.filter((row) => row.strategy?.execution_mode !== "manual_review" && row.strategy?.requires_more_evidence !== true).length;
  return (
    <div className="mb-4 rounded-lg border border-cyan-400/15 bg-cyan-400/[0.04] p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <span className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-md border border-cyan-400/20 bg-cyan-500/10 text-cyan-200">
            <Sparkles className="h-4 w-4" />
          </span>
          <div>
            <h2 className="text-sm font-semibold text-cyan-50">今日治理建议</h2>
            <p className="mt-1 text-xs text-slate-500">系统根据冲突、失败模式、陈旧概念和待确认队列，推荐最该处理的知识任务。</p>
          </div>
        </div>
        {onBatch && rows.length ? (
          <button
            className="rounded border border-emerald-300/25 bg-emerald-400/10 px-2 py-1 text-[11px] text-emerald-100 hover:bg-emerald-400/15 disabled:opacity-50"
            onClick={() => onBatch(rows)}
            disabled={Boolean(busyKey)}
            title={`Batch-safe recommendations: ${Math.min(batchableCount, 3)}`}
          >
            {busyKey ? "处理中" : "批量执行前三条"}
          </button>
        ) : null}
      </div>
      <div className="mt-3 space-y-2">
        {rows.slice(0, 5).map((row) => {
          const tone =
            row.priority === "high"
              ? "border-rose-300/25 bg-rose-400/10 text-rose-100"
              : row.priority === "medium"
                ? "border-amber-300/25 bg-amber-400/10 text-amber-100"
                : "border-cyan-300/25 bg-cyan-400/10 text-cyan-100";
          const score = row.priority_score == null ? null : Number(row.priority_score).toFixed(0);
          const strategyMode = row.strategy?.execution_mode ?? "normal";
          return (
            <div key={row.id} className="rounded-md border border-white/10 bg-slate-950/45 p-3">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="line-clamp-2 text-xs font-semibold text-cyan-50">{row.title}</div>
                  <div className="mt-1 line-clamp-2 text-[11px] text-slate-500">{row.reason}</div>
                  <div className="mt-2 flex flex-wrap gap-1.5 text-[10px]">
                    {score ? (
                      <span className="rounded border border-cyan-300/20 bg-cyan-400/10 px-1.5 py-0.5 text-cyan-100">score {score}</span>
                    ) : null}
                    <span className="rounded border border-slate-300/15 bg-slate-400/10 px-1.5 py-0.5 text-slate-300">{strategyMode}</span>
                    <span className="rounded border border-slate-300/15 bg-slate-400/10 px-1.5 py-0.5 text-slate-400">
                      {row.strategy?.reason ?? "default_strategy"}
                    </span>
                    {row.strategy?.requires_more_evidence ? (
                      <span className="rounded border border-amber-300/25 bg-amber-400/10 px-1.5 py-0.5 text-amber-100">needs evidence</span>
                    ) : null}
                  </div>
                </div>
                <span className={`rounded border px-1.5 py-0.5 text-[10px] ${tone}`}>{row.priority}</span>
              </div>
              <div className="mt-2 flex items-center justify-between gap-2">
                <span className="truncate font-mono text-[11px] text-slate-600">{row.source}</span>
                <button
                  className="rounded border border-cyan-300/25 bg-cyan-400/10 px-2 py-1 text-[11px] text-cyan-100 hover:bg-cyan-400/15 disabled:opacity-50"
                  onClick={() => onRun(row)}
                  disabled={Boolean(busyKey)}
                  title={row.strategy?.execution_mode === "manual_review" ? "Manual review recommended by strategy policy" : "Run governance action"}
                >
                  {busyKey ? "处理中" : "执行"}
                </button>
              </div>
            </div>
          );
        })}
        {rows.length === 0 ? <div className="rounded-md border border-white/10 bg-slate-950/45 p-3 text-xs text-slate-500">暂无主动治理建议</div> : null}
      </div>
    </div>
  );
}

function GovernanceHistoryList({ rows }: { rows: GovernanceHistoryRow[] }) {
  return (
    <div className="mb-4 rounded-lg border border-white/10 bg-white/[0.03] p-4">
      <div className="flex items-start gap-3">
        <span className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-md border border-violet-400/20 bg-violet-500/10 text-violet-200">
          <Clock3 className="h-4 w-4" />
        </span>
        <div>
          <h2 className="text-sm font-semibold text-cyan-50">治理历史</h2>
          <p className="mt-1 text-xs text-slate-500">最近的确认、拒绝、归档、重新验证和 playbook 生成记录。</p>
        </div>
      </div>
      <div className="mt-3 space-y-2">
        {rows.slice(0, 6).map((row) => (
          <div key={row.governance_id} className="rounded-md border border-white/10 bg-slate-950/45 p-2">
            <div className="flex items-center justify-between gap-2">
              <span className="rounded border border-violet-300/20 bg-violet-400/10 px-1.5 py-0.5 font-mono text-[10px] text-violet-100">{row.action}</span>
              <span className="text-[11px] text-slate-500">{row.created_at?.slice(0, 16).replace("T", " ")}</span>
            </div>
            <div className="mt-2 line-clamp-2 text-xs text-cyan-50">{row.summary}</div>
            <div className="mt-1 truncate font-mono text-[11px] text-slate-600">{compactPath(row.report_path)}</div>
          </div>
        ))}
        {rows.length === 0 ? <div className="rounded-md border border-white/10 bg-slate-950/45 p-3 text-xs text-slate-500">暂无治理历史</div> : null}
      </div>
    </div>
  );
}

function MiniHealthCard({ label, value, tone }: { label: string; value: string | number; tone: "cyan" | "emerald" | "amber" | "rose" }) {
  const toneClass =
    tone === "rose"
      ? "text-rose-200"
      : tone === "amber"
        ? "text-amber-200"
        : tone === "emerald"
          ? "text-emerald-200"
          : "text-cyan-200";
  return (
    <div className="rounded-md border border-white/10 bg-white/[0.03] p-2">
      <div className="text-[11px] text-slate-500">{label}</div>
      <div className={`mt-1 truncate text-sm font-semibold ${toneClass}`}>{value}</div>
    </div>
  );
}

function RankedList({
  title,
  subtitle,
  icon: Icon,
  rows,
  empty,
  tone,
  actionLabel,
  onAction,
  busyKey,
}: {
  title: string;
  subtitle: string;
  icon: typeof ShieldAlert;
  rows: RankedRow[];
  empty: string;
  tone: "rose" | "amber";
  actionLabel?: string;
  onAction?: (row: RankedRow) => void;
  busyKey?: string | null;
}) {
  const toneClass = tone === "rose" ? "border-rose-400/20 bg-rose-500/10 text-rose-200" : "border-amber-400/20 bg-amber-500/10 text-amber-200";
  return (
    <div className="rounded-lg border border-white/10 bg-white/[0.03] p-4">
      <div className="flex items-start gap-3">
        <span className={`flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-md border ${toneClass}`}>
          <Icon className="h-4 w-4" />
        </span>
        <div>
          <h3 className="text-sm font-semibold text-cyan-50">{title}</h3>
          <p className="mt-1 text-xs text-slate-500">{subtitle}</p>
        </div>
      </div>
      <div className="mt-3 space-y-2">
        {rows.slice(0, 6).map((row, index) => {
          const label = row.reason || row.pattern || "unknown";
          return (
            <div key={`${label}-${index}`} className="rounded-md border border-white/10 bg-slate-950/45 p-2">
              <div className="flex items-center justify-between gap-2">
                <span className="truncate font-mono text-xs text-cyan-100">{label}</span>
                <span className="rounded border border-white/10 px-1.5 py-0.5 text-[11px] text-slate-300">{row.count}</span>
              </div>
              <div className="mt-1 truncate text-[11px] text-slate-500">{compactPath(row.latest_path || row.examples?.[0])}</div>
              {actionLabel && onAction ? (
                <button
                  className="mt-2 rounded border border-amber-300/25 bg-amber-400/10 px-2 py-1 text-[11px] text-amber-100 transition hover:bg-amber-400/15 disabled:opacity-50"
                  onClick={() => onAction(row)}
                  disabled={Boolean(busyKey)}
                >
                  {busyKey ? "处理中" : actionLabel}
                </button>
              ) : null}
            </div>
          );
        })}
        {rows.length === 0 ? <div className="rounded-md border border-white/10 bg-slate-950/45 p-3 text-xs text-slate-500">{empty}</div> : null}
      </div>
    </div>
  );
}

function QueueList({
  title,
  subtitle,
  icon: Icon,
  rows,
  empty,
  onGovernance,
  busyKey,
}: {
  title: string;
  subtitle: string;
  icon: typeof ListChecks;
  rows: QueueRow[];
  empty: string;
  onGovernance?: (action: Extract<GovernanceAction, "confirm_pending" | "reject_pending" | "defer_pending">, row: QueueRow, label: string) => void;
  busyKey?: string | null;
}) {
  return (
    <div className="mt-4 rounded-lg border border-white/10 bg-white/[0.03] p-4">
      <div className="flex items-start gap-3">
        <span className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-md border border-cyan-400/20 bg-cyan-500/10 text-cyan-200">
          <Icon className="h-4 w-4" />
        </span>
        <div>
          <h3 className="text-sm font-semibold text-cyan-50">{title}</h3>
          <p className="mt-1 text-xs text-slate-500">{subtitle}</p>
        </div>
      </div>
      <div className="mt-3 space-y-2">
        {rows.slice(0, 8).map((row, index) => (
          <div key={`${row.path}-${index}`} className="rounded-md border border-white/10 bg-slate-950/45 p-2">
            <div className="line-clamp-2 text-xs font-medium text-cyan-50">{row.summary}</div>
            <div className="mt-1 flex items-center justify-between gap-2 text-[11px] text-slate-500">
              <span>{row.reason}</span>
              <span>{row.date}</span>
            </div>
            <div className="mt-1 truncate font-mono text-[11px] text-slate-600">{compactPath(row.path)}</div>
            {onGovernance ? (
              <div className="mt-2 flex flex-wrap gap-2">
                <button
                  className="rounded border border-emerald-300/25 bg-emerald-400/10 px-2 py-1 text-[11px] text-emerald-100 hover:bg-emerald-400/15 disabled:opacity-50"
                  onClick={() => onGovernance("confirm_pending", row, "确认待沉淀知识")}
                  disabled={Boolean(busyKey)}
                >
                  确认
                </button>
                <button
                  className="rounded border border-rose-300/25 bg-rose-400/10 px-2 py-1 text-[11px] text-rose-100 hover:bg-rose-400/15 disabled:opacity-50"
                  onClick={() => onGovernance("reject_pending", row, "拒绝待沉淀知识")}
                  disabled={Boolean(busyKey)}
                >
                  拒绝
                </button>
                <button
                  className="rounded border border-slate-300/20 bg-slate-400/10 px-2 py-1 text-[11px] text-slate-200 hover:bg-slate-400/15 disabled:opacity-50"
                  onClick={() => onGovernance("defer_pending", row, "稍后处理待沉淀知识")}
                  disabled={Boolean(busyKey)}
                >
                  稍后
                </button>
              </div>
            ) : null}
          </div>
        ))}
        {rows.length === 0 ? <div className="rounded-md border border-white/10 bg-slate-950/45 p-3 text-xs text-slate-500">{empty}</div> : null}
      </div>
    </div>
  );
}

function StaleConceptList({
  rows,
  onGovernance,
  busyKey,
}: {
  rows: StaleConceptRow[];
  onGovernance?: (action: Extract<GovernanceAction, "revalidate_stale" | "archive_stale">, row: StaleConceptRow, label: string) => void;
  busyKey?: string | null;
}) {
  return (
    <div className="mt-4 rounded-lg border border-white/10 bg-white/[0.03] p-4">
      <div className="flex items-start gap-3">
        <span className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-md border border-amber-400/20 bg-amber-500/10 text-amber-200">
          <Clock3 className="h-4 w-4" />
        </span>
        <div>
          <h3 className="text-sm font-semibold text-cyan-50">陈旧概念</h3>
          <p className="mt-1 text-xs text-slate-500">超过生命周期或缺少近期验证的知识。</p>
        </div>
      </div>
      <div className="mt-3 space-y-2">
        {rows.slice(0, 8).map((row, index) => (
          <div key={`${row.path}-${index}`} className="rounded-md border border-white/10 bg-slate-950/45 p-2">
            <div className="line-clamp-2 text-xs font-medium text-cyan-50">{row.summary}</div>
            <div className="mt-1 flex items-center justify-between gap-2 text-[11px] text-slate-500">
              <span>{row.reason}</span>
              <span>{row.date}</span>
            </div>
            <div className="mt-1 truncate font-mono text-[11px] text-slate-600">{compactPath(row.path)}</div>
            {onGovernance ? (
              <div className="mt-2 flex flex-wrap gap-2">
                <button
                  className="rounded border border-cyan-300/25 bg-cyan-400/10 px-2 py-1 text-[11px] text-cyan-100 hover:bg-cyan-400/15 disabled:opacity-50"
                  onClick={() => onGovernance("revalidate_stale", row, "重新验证陈旧概念")}
                  disabled={Boolean(busyKey)}
                >
                  重新验证
                </button>
                <button
                  className="rounded border border-amber-300/25 bg-amber-400/10 px-2 py-1 text-[11px] text-amber-100 hover:bg-amber-400/15 disabled:opacity-50"
                  onClick={() => onGovernance("archive_stale", row, "归档陈旧概念")}
                  disabled={Boolean(busyKey)}
                >
                  归档
                </button>
              </div>
            ) : null}
          </div>
        ))}
        {rows.length === 0 ? <div className="rounded-md border border-white/10 bg-slate-950/45 p-3 text-xs text-slate-500">暂无陈旧概念</div> : null}
      </div>
    </div>
  );
}
