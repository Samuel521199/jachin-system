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

type TrustGovernanceReview = {
  summary?: {
    recommended_count?: number;
    executed_count?: number;
    converted_count?: number;
    pending_count?: number;
    failed_count?: number;
    conversion_rate?: number;
  };
  pending?: Array<Record<string, unknown>>;
  converted?: Array<Record<string, unknown>>;
  failed?: Array<Record<string, unknown>>;
  follow_up_queue?: Array<Record<string, unknown>>;
  next_actions?: GovernanceRecommendationRow[];
  recent?: Array<Record<string, unknown>>;
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

type AutoGovernancePolicy = {
  schema_version?: number;
  mode: "off" | "manual" | "safe_auto" | string;
  max_items?: number;
  updated_at?: string;
  allowed_modes?: string[];
};

type AutoGovernanceLatest = {
  auto_governance_id?: string;
  created_at?: string;
  source?: string;
  mode?: string;
  requested_count?: number;
  selected_count?: number;
  executed_count?: number;
  failed_count?: number;
  skipped?: Array<Record<string, unknown>>;
  report_path?: string;
};

type AutoGovernanceTrendRow = {
  date: string;
  runs: number;
  executed: number;
  failed: number;
  skipped: number;
  retry_limited: number;
};

type AutoGovernanceRecommendation = {
  current_mode?: string;
  recommended_mode?: string;
  severity?: "healthy" | "warning" | "opportunity" | "info" | string;
  should_change?: boolean;
  reasons?: string[];
  metrics?: Record<string, number | string>;
};

type AutoGovernanceModeHistoryTrendRow = {
  date: string;
  records: number;
  change_recommended: number;
  safe_auto_recommended: number;
  manual_recommended: number;
  off_recommended: number;
  auto_failed: number;
  retry_limited: number;
  trust_next_actions: number;
};

type AutoGovernanceModeHistory = {
  latest?: Record<string, unknown>;
  summary?: {
    total_records?: number;
    last_30_records?: number;
    last_30_change_recommended?: number;
    last_30_safe_auto_recommended?: number;
    last_30_manual_recommended?: number;
    last_30_off_recommended?: number;
    last_30_auto_failed?: number;
    last_30_retry_limited?: number;
    last_30_trust_next_actions?: number;
    risk_direction?: string;
    error?: string;
  };
  trends?: {
    days_7?: AutoGovernanceModeHistoryTrendRow[];
    days_14?: AutoGovernanceModeHistoryTrendRow[];
    days_30?: AutoGovernanceModeHistoryTrendRow[];
  };
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

type SuccessPathHealth = {
  summary?: {
    total_paths?: number;
    reliable_count?: number;
    degraded_count?: number;
    unproven_count?: number;
    total_use_count?: number;
    success_count?: number;
    failure_count?: number;
    success_rate?: number;
  };
  reliable_paths?: ArtifactUsageRow[];
  degraded_paths?: ArtifactUsageRow[];
  unproven_paths?: ArtifactUsageRow[];
};

type MemoryTrustRow = {
  memory_id?: string;
  memory_type?: string;
  trust_state?: string;
  trust_reason?: string;
  trust_weight?: number;
  recall_allowed?: boolean;
  review_required?: boolean;
  confidence?: number;
  updated_at_ms?: number;
  review_priority?: number;
  content?: string;
  content_preview?: string;
};

type MemoryTrustSummary = {
  summary?: {
    total_count?: number;
    confirmed_count?: number;
    floating_count?: number;
    conflicted_count?: number;
    rejected_count?: number;
    expired_count?: number;
    recall_blocked_count?: number;
    error?: string;
  };
  requires_confirmation?: MemoryTrustRow[];
  review_queue?: MemoryTrustRow[];
  recent_floating?: MemoryTrustRow[];
  recent_rejected?: MemoryTrustRow[];
  recent_confirmed?: MemoryTrustRow[];
  analytics?: {
    summary?: {
      pattern_count?: number;
      rejected_pattern_count?: number;
      promotion_candidate_count?: number;
      conflict_cluster_count?: number;
      floating_hotspot_count?: number;
      stale_confirmed_count?: number;
    };
    rejected_patterns?: Array<Record<string, unknown>>;
    promotion_candidates?: Array<Record<string, unknown>>;
    conflict_clusters?: Array<Record<string, unknown>>;
    floating_hotspots?: Array<Record<string, unknown>>;
    stale_confirmed?: Array<Record<string, unknown>>;
  };
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
  success_path_health?: SuccessPathHealth;
  memory_trust?: MemoryTrustSummary;
  artifact_usage_trends?: {
    days_7: ArtifactUsageTrendRow[];
    days_14: ArtifactUsageTrendRow[];
    days_30: ArtifactUsageTrendRow[];
  };
  artifact_usage_attribution?: ArtifactUsageAttribution;
  artifact_usage_recommendations?: Record<string, unknown>[];
  governance_recommendations: GovernanceRecommendationRow[];
  governance_effectiveness?: GovernanceEffectiveness;
  trust_governance_review?: TrustGovernanceReview;
  governance_effectiveness_trends?: {
    days_7: GovernanceEffectivenessTrendRow[];
    days_14: GovernanceEffectivenessTrendRow[];
    days_30: GovernanceEffectivenessTrendRow[];
  };
  governance_effectiveness_attribution?: GovernanceEffectivenessAttribution;
  governance_strategy_policy?: GovernanceStrategyPolicy;
  memory_governance_auto_policy?: AutoGovernancePolicy;
  memory_governance_auto_latest?: AutoGovernanceLatest;
  memory_governance_auto_trends?: {
    days_7: AutoGovernanceTrendRow[];
    days_14: AutoGovernanceTrendRow[];
    days_30: AutoGovernanceTrendRow[];
  };
  memory_governance_auto_recommendation?: AutoGovernanceRecommendation;
  memory_governance_auto_mode_history?: AutoGovernanceModeHistory;
  health: {
    quality_score: number;
    risk_level: "low" | "medium" | "high" | string;
    stale_concept_count: number;
    pending_confirmation_count: number;
    failure_pattern_count: number;
    governance_history_count?: number;
    artifact_usage_count?: number;
    memory_trust_confirmed_count?: number;
    memory_trust_floating_count?: number;
    memory_trust_conflicted_count?: number;
    memory_trust_rejected_count?: number;
    memory_trust_expired_count?: number;
    memory_trust_rejected_pattern_count?: number;
    memory_trust_promotion_candidate_count?: number;
    memory_trust_stale_confirmed_count?: number;
    success_path_reliable_count?: number;
    success_path_degraded_count?: number;
    artifact_low_success_count?: number;
    artifact_stale_unused_count?: number;
    recommendation_count?: number;
    governance_effectiveness_score?: number;
    trust_governance_conversion_rate?: number;
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
  | "merge_artifact_draft"
  | "confirm_memory"
  | "reject_memory"
  | "mark_memory_conflicted"
  | "correct_memory"
  | "review_rejected_memory_pattern"
  | "promote_memory_pattern"
  | "revalidate_confirmed_memory";

const actionLabels: Record<ActionKey, { title: string; subtitle: string; icon: typeof Sparkles; payload?: Record<string, unknown> }> = {
  pipeline: {
    title: "杩愯娑堝寲绠＄嚎",
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
    title: "Weekly Review",
    subtitle: "妫€鏌ラ檲鏃ф蹇点€佸啿绐併€佺敓鍛藉懆鏈熷拰澶辫触妯″紡",
    icon: Repeat2,
    payload: { stale_after_days: 30 },
  },
  "graph-sync": {
    title: "鍚屾鏈湴鍥捐氨",
    subtitle: "Markdown Wiki -> Graph nodes / edges",
    icon: GitBranch,
  },
  "connector-sync": {
    title: "Sync Graph Connectors",
    subtitle: "LocalJson / Cognee / Graphiti connector status",
    icon: Network,
    payload: { graph_connector_ids: ["local_json_graph", "cognee", "graphiti"] },
  },
  "artifact-curator": {
    title: "杩愯 Artifact Curator",
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
  if (!path) return "鏆傛棤";
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
      <div className="mt-1 break-all font-mono text-xs text-cyan-100/90">{path || "鏆傛棤"}</div>
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
      setNotice(`Memory Growth 鐘舵€佸姞杞藉け璐ワ細${String(e)}`);
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
      setNotice(`${config.title} completed; stats and reports refreshed.`);
    } catch (e) {
      setNotice(`${actionLabels[action].title} 澶辫触锛?{String(e)}`);
    } finally {
      setBusyAction(null);
    }
  };

  const runGovernance = async (action: GovernanceAction, item: Record<string, unknown>, label: string) => {
    const busyKey = `${action}:${String(item.memory_id || item.id || item.path || item.pattern || item.reason || label)}`;
    setBusyGovernance(busyKey);
    setNotice(null);
    try {
      await callMemoryGrowth("/api/v1/memory-growth/governance", {
        method: "POST",
        body: JSON.stringify({ action, item, note: label }),
      });
      const next = await callMemoryGrowth<MemoryGrowthStatus>("/api/v1/memory-growth/status");
      setStatus(next);
      setNotice(`${label} completed; governance evidence written to Memory Growth.`);
    } catch (e) {
      setNotice(`${label} 澶辫触锛?{String(e)}`);
    } finally {
      setBusyGovernance(null);
    }
  };

  const runBatchGovernance = async (rows: GovernanceRecommendationRow[], label: string) => {
    const limit = rows.some((row) => row.source === "memory_trust_review_queue") ? 10 : 3;
    const selected = rows
      .filter((row) => row.strategy?.execution_mode !== "manual_review" && row.strategy?.requires_more_evidence !== true)
      .slice(0, limit);
    if (!selected.length) {
      setNotice("Current governance recommendations require manual review; no batch action was run.");
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
      setNotice(`${label} completed; batch governance evidence written to Memory Growth.`);
    } catch (e) {
      setNotice(`${label} 澶辫触锛?{String(e)}`);
    } finally {
      setBusyGovernance(null);
    }
  };

  const saveAutoGovernanceMode = async (mode: "off" | "manual" | "safe_auto") => {
    setBusyGovernance(`auto-policy:${mode}`);
    setNotice(null);
    try {
      const currentLimit = monitoring?.memory_governance_auto_policy?.max_items ?? 5;
      const payload = await callMemoryGrowth<{ status: MemoryGrowthStatus }>("/api/v1/memory-growth/auto-governance-policy", {
        method: "POST",
        body: JSON.stringify({ mode, max_items: currentLimit }),
      });
      setStatus(payload.status);
      setNotice(`Memory governance auto mode saved: ${mode}.`);
    } catch (e) {
      setNotice(`Memory governance auto mode save failed: ${String(e)}`);
    } finally {
      setBusyGovernance(null);
    }
  };

  return (
    <div className="console-fiber-host console-holo-slab flex h-full min-h-0 flex-col overflow-hidden p-4">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-cyan-400/15 pb-4">
        <div>
          <p className="text-xs uppercase tracking-[0.32em] text-cyan-300/70">AI Self-Growing Knowledge</p>
          <h1 className="mt-1 text-2xl font-semibold text-cyan-50">Memory Growth 鎺у埗鍙</h1>
          <p className="mt-1 text-sm text-slate-400">
            鍘熷璇佹嵁銆佹椂闂磋蹇嗐€丮arkdown Wiki銆佹柟娉曡娌夋穩鍜屽浘璋卞悓姝ョ殑缁熶竴鍏ュ彛銆?          </p>
        </div>
        <button
          className="inline-flex h-10 items-center gap-2 rounded-md border border-cyan-400/25 bg-cyan-500/10 px-3 text-sm text-cyan-100 transition hover:bg-cyan-400/15 disabled:opacity-50"
          onClick={() => void load(true)}
          disabled={loading || Boolean(busyAction)}
        >
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
          鍒锋柊鐘舵€?        </button>
      </div>

      {notice ? <div className="mt-3 rounded-md border border-cyan-400/20 bg-cyan-500/10 px-3 py-2 text-sm text-cyan-100">{notice}</div> : null}

      <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-4 xl:grid-cols-7">
        <StatCard label="鍘熷璇佹嵁" value={numberText(counts?.raw_events)} Icon={Database} />
        <StatCard label="Concepts" value={numberText(counts?.concepts)} Icon={BrainCircuit} tone="emerald" />
        <StatCard label="Playbooks" value={numberText(counts?.playbooks)} Icon={FileText} tone="violet" />
        <StatCard label="杈撳嚭鍥炴祦" value={numberText(counts?.outputs)} Icon={Activity} tone="cyan" />
        <StatCard label="鍐茬獊寰呭" value={numberText(counts?.conflicts)} Icon={Repeat2} tone={counts?.conflicts ? "rose" : "amber"} />
        <StatCard label="鍥捐氨鑺傜偣" value={numberText(counts?.graph_nodes)} Icon={GitBranch} tone="emerald" />
        <StatCard label="Graph edges" value={numberText(counts?.graph_edges)} Icon={Network} tone="violet" />
      </div>

      <div className="mt-4 grid min-h-0 flex-1 grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_22rem]">
        <section className="min-h-0 overflow-auto rounded-lg border border-cyan-400/15 bg-slate-950/55 p-4">
          <div className="flex items-center justify-between gap-3">
            <div>
              <h2 className="text-base font-semibold text-cyan-50">澧為暱鍔ㄤ綔</h2>
              <p className="mt-1 text-xs text-slate-500">鎵€鏈夊姩浣滈兘浼氬洖鍐欐姤鍛婅矾寰勶紝鍚庣画 Evidence / Wiki 椤甸潰鍙互缁х画杩借釜銆</p>
            </div>
            <span className="rounded-md border border-emerald-400/20 bg-emerald-500/10 px-2 py-1 text-xs text-emerald-200">
              璐ㄩ噺鍒?{qualityScore}%
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
                <h2 className="text-base font-semibold text-cyan-50">澧為暱瓒嬪娍</h2>
                <p className="mt-1 text-xs text-slate-500">瑙傚療鍘熷璇佹嵁銆佹蹇点€佹柟娉曡銆佽緭鍑哄拰鍐茬獊鏄惁鍦ㄦ寔缁矇娣€銆</p>
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
                    {days}澶?                  </button>
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
                    labelFormatter={(label) => `鏃ユ湡 ${label}`}
                  />
                  <Line type="monotone" dataKey="raw_events" name="鍘熷璇佹嵁" stroke="#22d3ee" strokeWidth={2} dot={false} />
                  <Line type="monotone" dataKey="concepts" name="姒傚康" stroke="#34d399" strokeWidth={2} dot={false} />
                  <Line type="monotone" dataKey="playbooks" name="Playbooks" stroke="#a78bfa" strokeWidth={2} dot={false} />
                  <Line type="monotone" dataKey="outputs" name="杈撳嚭" stroke="#fbbf24" strokeWidth={2} dot={false} />
                  <Line type="monotone" dataKey="conflicts" name="鍐茬獊" stroke="#fb7185" strokeWidth={2} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>

          <div className="mt-4 grid grid-cols-1 gap-3 lg:grid-cols-2">
            <RankedList
              title="鍐茬獊绫诲瀷"
              subtitle="闇€瑕佹不鐞嗙殑鐭ヨ瘑鍐茬獊鍒嗗竷"
              icon={ShieldAlert}
              rows={monitoring?.conflict_types ?? []}
              empty="鏆傛棤鍐茬獊"
              tone="rose"
            />
            <RankedList
              title="澶辫触妯″紡"
              subtitle="浠庡け璐?TurnClosure 鍜屽啿绐佷腑鑱氬悎"
              icon={AlertTriangle}
              rows={monitoring?.failure_patterns ?? []}
              empty="鏆傛棤澶辫触妯″紡"
              tone="amber"
              actionLabel="鐢熸垚 Playbook"
              onAction={(row) => void runGovernance("generate_failure_playbook", row as unknown as Record<string, unknown>, "Generate failure playbook")}
              busyKey={busyGovernance}
            />
          </div>

          <div className="mt-4 rounded-lg border border-white/10 bg-white/[0.03] p-4">
            <div className="text-xs text-slate-500">Memory Growth Root</div>
            <div className="mt-1 break-all font-mono text-xs text-cyan-100/90">{status?.root || "绛夊緟 L3 杩斿洖"}</div>
          </div>
        </section>

        <section className="min-h-0 overflow-auto rounded-lg border border-cyan-400/15 bg-slate-950/55 p-4">
          <div className="mb-4 grid grid-cols-3 gap-2">
            <MiniHealthCard label="椋庨櫓" value={health?.risk_level || "unknown"} tone={health?.risk_level === "high" ? "rose" : health?.risk_level === "medium" ? "amber" : "emerald"} />
            <MiniHealthCard label="闄堟棫" value={health?.stale_concept_count ?? 0} tone="amber" />
            <MiniHealthCard label="Pending" value={health?.pending_confirmation_count ?? 0} tone="cyan" />
          </div>
          <MemoryTrustCard
            data={monitoring?.memory_trust}
            busyKey={busyGovernance}
            onRun={(action, item, label) => void runGovernance(action, item, label)}
            onBatch={(action, rows, label) =>
              void runBatchGovernance(
                rows.map((row) => ({
                  id: `${action}:${row.memory_id}`,
                  priority: "medium",
                  title: label,
                  reason: row.trust_reason || row.content_preview || "",
                  action,
                  item: row as unknown as Record<string, unknown>,
                  source: "memory_trust_review_queue",
                })),
                label,
              )
            }
          />
          <TrustGovernanceReviewCard
            data={monitoring?.trust_governance_review}
            busyKey={busyGovernance}
            onRun={(row) => void runGovernance(row.action, row.item, row.title)}
          />
          <AutoGovernancePolicyCard
            policy={monitoring?.memory_governance_auto_policy}
            latest={monitoring?.memory_governance_auto_latest}
            trends={monitoring?.memory_governance_auto_trends?.days_30 ?? []}
            recommendation={monitoring?.memory_governance_auto_recommendation}
            history={monitoring?.memory_governance_auto_mode_history}
            busyKey={busyGovernance}
            onModeChange={(mode) => void saveAutoGovernanceMode(mode)}
          />
          <GovernanceEffectivenessCard
            data={monitoring?.governance_effectiveness}
            trends={monitoring?.governance_effectiveness_trends?.days_30 ?? []}
            attribution={monitoring?.governance_effectiveness_attribution}
            policy={monitoring?.governance_strategy_policy}
          />
          <ArtifactUsageCard
            rows={monitoring?.artifact_usage ?? []}
            successPathHealth={monitoring?.success_path_health}
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
            onBatch={(rows) => void runBatchGovernance(rows, "鎵归噺鎵ц娌荤悊寤鸿")}
          />
          <GovernanceHistoryList rows={monitoring?.governance_history ?? []} />

          <h2 className="text-base font-semibold text-cyan-50">鏈€鏂版姤鍛</h2>
          <p className="mt-1 text-xs text-slate-500">杩欓噷鏄剧ず姣忎釜闃舵鏈€鏂颁骇鐗╋紝鏂逛究杩芥函鍜屾帓鏌ャ€</p>
          <div className="mt-4 space-y-3">
            <LatestPath label="Pipeline Report" path={status?.latest?.pipeline_report} />
            <LatestPath label="Weekly Review" path={status?.latest?.weekly_report} />
            <LatestPath label="Graph Event" path={status?.latest?.graph_event} />
            <LatestPath label="Connector Index" path={status?.latest?.connector_index} />
            <LatestPath label="Artifact Curator" path={status?.latest?.artifact_curator_report} />
          </div>
          <div className="mt-4 rounded-md border border-white/10 bg-white/[0.03] p-3">
            <div className="text-xs text-slate-500">鍙敤鍔ㄤ綔</div>
            <div className="mt-2 flex flex-wrap gap-2">
              {(status?.available_actions ?? []).map((item) => (
                <span key={item} className="rounded border border-cyan-300/20 bg-cyan-400/10 px-2 py-1 font-mono text-[11px] text-cyan-100">
                  {item}
                </span>
              ))}
              {!status?.available_actions?.length ? <span className="text-xs text-slate-500">绛夊緟鐘舵€佸姞杞</span> : null}
            </div>
          </div>

          <QueueList
            title="Pending confirmations"
            subtitle="Preferences, facts, or pending decisions that still need review"
            icon={ListChecks}
            rows={monitoring?.pending_confirmation_queue ?? []}
            empty="No pending confirmations"
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

function AutoGovernancePolicyCard({
  policy,
  latest,
  trends,
  recommendation,
  history,
  busyKey,
  onModeChange,
}: {
  policy?: AutoGovernancePolicy;
  latest?: AutoGovernanceLatest;
  trends: AutoGovernanceTrendRow[];
  recommendation?: AutoGovernanceRecommendation;
  history?: AutoGovernanceModeHistory;
  busyKey?: string | null;
  onModeChange?: (mode: "off" | "manual" | "safe_auto") => void;
}) {
  const mode = policy?.mode ?? "safe_auto";
  const modeTone =
    mode === "safe_auto"
      ? "border-emerald-300/25 bg-emerald-400/10 text-emerald-100"
      : mode === "manual"
        ? "border-amber-300/25 bg-amber-400/10 text-amber-100"
        : "border-slate-300/20 bg-slate-400/10 text-slate-200";
  const skipped = latest?.skipped ?? [];
  const retryLimited = skipped.filter((item) => item?.reason === "auto_retry_limit_reached").length;
  const historySummary = history?.summary ?? {};
  const historyTrends = history?.trends?.days_30 ?? [];
  const recTone =
    recommendation?.severity === "healthy"
      ? "border-emerald-300/20 bg-emerald-400/10 text-emerald-100"
      : recommendation?.severity === "warning"
        ? "border-amber-300/25 bg-amber-400/10 text-amber-100"
        : recommendation?.severity === "opportunity"
          ? "border-cyan-300/25 bg-cyan-400/10 text-cyan-100"
          : "border-slate-300/15 bg-slate-400/5 text-slate-300";
  return (
    <div className="mb-4 rounded-lg border border-white/10 bg-white/[0.03] p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-cyan-50">Memory Governance Auto Policy</h2>
          <p className="mt-1 text-xs text-slate-500">Controls whether safe trust-governance follow-ups run during Daily Review.</p>
        </div>
        <span className={`rounded border px-2 py-1 text-xs ${modeTone}`}>{mode}</span>
      </div>

      <div className="mt-3 grid grid-cols-4 gap-2">
        <MiniHealthCard label="max/run" value={policy?.max_items ?? 5} tone="cyan" />
        <MiniHealthCard label="executed" value={latest?.executed_count ?? 0} tone="emerald" />
        <MiniHealthCard label="failed" value={latest?.failed_count ?? 0} tone={(latest?.failed_count ?? 0) ? "rose" : "cyan"} />
        <MiniHealthCard label="skipped" value={skipped.length} tone={skipped.length ? "amber" : "cyan"} />
      </div>

      <div className="mt-3 flex flex-wrap gap-2">
        {(["off", "manual", "safe_auto"] as const).map((item) => (
          <button
            key={item}
            className={`rounded border px-2 py-1 text-[11px] transition disabled:opacity-50 ${
              mode === item
                ? "border-cyan-300/35 bg-cyan-400/15 text-cyan-100"
                : "border-slate-300/15 bg-slate-400/5 text-slate-300 hover:bg-slate-400/10"
            }`}
            disabled={Boolean(busyKey)}
            onClick={() => onModeChange?.(item)}
          >
            {busyKey === `auto-policy:${item}` ? "Saving..." : item}
          </button>
        ))}
      </div>

      <div className={`mt-3 rounded-md border p-3 ${recTone}`}>
        <div className="flex items-center justify-between gap-3">
          <div className="text-xs font-semibold">Mode recommendation</div>
          <span className="rounded border border-white/10 bg-black/10 px-2 py-0.5 text-[11px]">
            {recommendation?.should_change ? `${recommendation?.current_mode} -> ${recommendation?.recommended_mode}` : recommendation?.recommended_mode || mode}
          </span>
        </div>
        <div className="mt-2 space-y-1 text-[11px]">
          {(recommendation?.reasons?.length ? recommendation.reasons : ["no recommendation yet"]).slice(0, 3).map((item) => (
            <div key={item}>{item}</div>
          ))}
        </div>
      </div>

      <div className="mt-3 rounded-md border border-white/10 bg-slate-950/45 p-3">
        <div className="text-xs font-semibold text-cyan-100">Latest run</div>
        <div className="mt-2 grid grid-cols-2 gap-2 text-[11px] text-slate-400">
          <div>source: {latest?.source || "-"}</div>
          <div>created: {latest?.created_at || "-"}</div>
          <div>requested: {latest?.requested_count ?? 0}</div>
          <div>selected: {latest?.selected_count ?? 0}</div>
          <div>retry limited: {retryLimited}</div>
          <div className="truncate">report: {compactPath(latest?.report_path)}</div>
        </div>
      </div>

      <div className="mt-3 rounded-md border border-white/10 bg-slate-950/45 p-3">
        <div className="flex items-center justify-between gap-3">
          <div className="text-xs font-semibold text-cyan-100">Mode history</div>
          <span className="rounded border border-white/10 bg-black/10 px-2 py-0.5 text-[11px] text-slate-300">
            {historySummary.risk_direction || "unknown"}
          </span>
        </div>
        <div className="mt-2 grid grid-cols-4 gap-2 text-[11px] text-slate-400">
          <div>30d records: {historySummary.last_30_records ?? 0}</div>
          <div>changes: {historySummary.last_30_change_recommended ?? 0}</div>
          <div>auto failed: {historySummary.last_30_auto_failed ?? 0}</div>
          <div>retry limited: {historySummary.last_30_retry_limited ?? 0}</div>
        </div>
      </div>

      <div className="mt-4 h-32 rounded-md border border-white/10 bg-slate-950/45 p-2">
        {trends.some((row) => row.runs || row.executed || row.failed || row.skipped) ? (
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
              <Line type="monotone" dataKey="executed" name="executed" stroke="#34d399" strokeWidth={2} dot={false} />
              <Line type="monotone" dataKey="skipped" name="skipped" stroke="#f59e0b" strokeWidth={1.5} dot={false} />
              <Line type="monotone" dataKey="failed" name="failed" stroke="#fb7185" strokeWidth={1.5} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        ) : (
          <div className="flex h-full items-center justify-center text-xs text-slate-500">No auto-governance trend yet.</div>
        )}
      </div>

      <div className="mt-4 h-28 rounded-md border border-white/10 bg-slate-950/45 p-2">
        {historyTrends.some((row) => row.records || row.change_recommended || row.auto_failed || row.retry_limited) ? (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={historyTrends} margin={{ top: 8, right: 10, bottom: 2, left: -24 }}>
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
              <Line type="monotone" dataKey="change_recommended" name="change rec" stroke="#f59e0b" strokeWidth={1.5} dot={false} />
              <Line type="monotone" dataKey="auto_failed" name="auto failed" stroke="#fb7185" strokeWidth={1.5} dot={false} />
              <Line type="monotone" dataKey="safe_auto_recommended" name="safe_auto rec" stroke="#34d399" strokeWidth={1.5} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        ) : (
          <div className="flex h-full items-center justify-center text-xs text-slate-500">No mode-history trend yet.</div>
        )}
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
          <h2 className="text-sm font-semibold text-cyan-50">娌荤悊鏁堟灉璇勫垎</h2>
          <p className="mt-1 text-xs text-slate-500">鏍规嵁娌荤悊鎴愬姛鐜囥€佺‘璁ゆ矇娣€銆丳laybook 浜у嚭銆佸け璐ラ噸鐜板拰鍐茬獊鍘嬪姏璁＄畻銆</p>
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
        <MiniHealthCard label="鎴愬姛" value={data?.success_count ?? 0} tone="emerald" />
        <MiniHealthCard label="澶辫触" value={data?.failure_count ?? 0} tone={(data?.failure_count ?? 0) ? "rose" : "cyan"} />
        <MiniHealthCard label="Playbook" value={data?.generated_playbook_count ?? 0} tone="amber" />
      </div>
      <div className="mt-3 space-y-1">
        {(data?.recommendations ?? ["No governance effectiveness data yet. Run governance actions first."]).slice(0, 2).map((item, index) => (
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
              <Line type="monotone" dataKey="score" name="娌荤悊璇勫垎" stroke="#22d3ee" strokeWidth={2} dot={{ r: 2 }} />
              <Line type="monotone" dataKey="conflict_pressure" name="鍐茬獊鍘嬪姏" stroke="#fb7185" strokeWidth={1.5} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        ) : (
          <div className="flex h-full items-center justify-center text-xs text-slate-500">绛夊緟 Weekly Review 鐢熸垚娌荤悊瓒嬪娍</div>
        )}
      </div>
      <div className="mt-3 grid grid-cols-1 gap-2">
        <AttributionList title="鏈€鏈夋晥鍔ㄤ綔" rows={attribution?.effective_actions ?? []} empty="鏆傛棤鏈夋晥鍔ㄤ綔褰掑洜" tone="emerald" />
        <AttributionList title="鍙嶅澶辫触鍔ㄤ綔" rows={attribution?.ineffective_actions ?? attribution?.repeated_failures ?? []} empty="鏆傛棤鍙嶅澶辫触鍔ㄤ綔" tone="rose" />
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
            <span className="text-slate-500">鎴愬姛 {row.success_count ?? 0} / 澶辫触 {row.failure_count ?? row.failed_count ?? 0}</span>
          </div>
        ))}
        {rows.length === 0 ? <div className="text-[11px] text-slate-500">{empty}</div> : null}
      </div>
    </div>
  );
}

function TrustGovernanceReviewCard({
  data,
  busyKey,
  onRun,
}: {
  data?: TrustGovernanceReview;
  busyKey?: string | null;
  onRun?: (row: GovernanceRecommendationRow) => void;
}) {
  const summary = data?.summary ?? {};
  const conversionRate = Number(summary.conversion_rate ?? 0);
  const tone = conversionRate >= 0.7
    ? "emerald"
    : conversionRate > 0
      ? "amber"
      : "cyan";
  return (
    <div className="mb-4 rounded-lg border border-white/10 bg-white/[0.03] p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-cyan-50">Trust Governance Review</h2>
          <p className="mt-1 text-xs text-slate-500">Tracks whether trust recommendations become review artifacts, method memory, or revalidation requests.</p>
        </div>
        <span className={`rounded border px-2 py-1 text-xs ${
          tone === "emerald"
            ? "border-emerald-300/25 bg-emerald-400/10 text-emerald-100"
            : tone === "amber"
              ? "border-amber-300/25 bg-amber-400/10 text-amber-100"
              : "border-cyan-300/25 bg-cyan-400/10 text-cyan-100"
        }`}>
          {Math.round(conversionRate * 100)}%
        </span>
      </div>
      <div className="mt-3 grid grid-cols-5 gap-2">
        <MiniHealthCard label="suggested" value={summary.recommended_count ?? 0} tone="cyan" />
        <MiniHealthCard label="executed" value={summary.executed_count ?? 0} tone="cyan" />
        <MiniHealthCard label="converted" value={summary.converted_count ?? 0} tone="emerald" />
        <MiniHealthCard label="pending" value={summary.pending_count ?? 0} tone={(summary.pending_count ?? 0) ? "amber" : "cyan"} />
        <MiniHealthCard label="failed" value={summary.failed_count ?? 0} tone={(summary.failed_count ?? 0) ? "rose" : "cyan"} />
      </div>
      {(data?.next_actions ?? []).length ? (
        <div className="mt-3 rounded-md border border-cyan-300/15 bg-cyan-400/5 p-3">
          <div className="mb-2 flex items-center justify-between gap-2">
            <span className="text-xs font-semibold text-cyan-100">Next Actions</span>
            <span className="text-[11px] text-slate-500">{data?.next_actions?.length ?? 0} executable</span>
          </div>
          <div className="space-y-2">
            {(data?.next_actions ?? []).slice(0, 3).map((row) => (
              <div key={row.id} className="flex items-center justify-between gap-3 rounded border border-white/10 bg-slate-950/45 p-2">
                <div className="min-w-0">
                  <div className="truncate text-xs font-semibold text-cyan-50">{row.title}</div>
                  <div className="mt-1 line-clamp-1 text-[11px] text-slate-500">{row.reason}</div>
                </div>
                {onRun ? (
                  <button
                    className="rounded border border-cyan-300/25 bg-cyan-400/10 px-2 py-1 text-[11px] text-cyan-100 hover:bg-cyan-400/15 disabled:opacity-50"
                    onClick={() => onRun(row)}
                    disabled={Boolean(busyKey)}
                  >
                    {busyKey ? "Running" : "Run"}
                  </button>
                ) : null}
              </div>
            ))}
          </div>
        </div>
      ) : null}
      <div className="mt-3 grid gap-2 md:grid-cols-2">
        {(data?.converted ?? []).slice(0, 3).map((row, index) => (
          <TrustGovernanceReviewRow key={`converted-${index}`} row={row} tone="emerald" label="Converted" />
        ))}
        {(data?.pending ?? []).slice(0, 3).map((row, index) => (
          <TrustGovernanceReviewRow key={`pending-${index}`} row={row} tone="amber" label="Pending" />
        ))}
        {(data?.failed ?? []).slice(0, 2).map((row, index) => (
          <TrustGovernanceReviewRow key={`failed-${index}`} row={row} tone="rose" label="Failed" />
        ))}
      </div>
      {!(data?.converted?.length || data?.pending?.length || data?.failed?.length) ? (
        <div className="mt-3 rounded-md border border-white/10 bg-slate-950/45 p-3 text-xs text-slate-500">
          No trust governance conversion records yet. Run a trust recommendation to start tracking.
        </div>
      ) : null}
    </div>
  );
}

function TrustGovernanceReviewRow({
  row,
  tone,
  label,
}: {
  row: Record<string, unknown>;
  tone: "emerald" | "amber" | "rose";
  label: string;
}) {
  const toneClass = tone === "emerald"
    ? "border-emerald-300/15 bg-emerald-400/5 text-emerald-100"
    : tone === "rose"
      ? "border-rose-300/15 bg-rose-400/5 text-rose-100"
      : "border-amber-300/15 bg-amber-400/5 text-amber-100";
  const summary = String(row.summary || row.pattern_key || row.artifact_path || row.report_path || "");
  return (
    <div className={`rounded border p-2 ${toneClass}`}>
      <div className="mb-1 flex items-center justify-between gap-2">
        <span className="rounded bg-slate-950/50 px-1.5 py-0.5 text-[10px]">{label}</span>
        <span className="truncate font-mono text-[10px] opacity-70">{String(row.action || "")}</span>
      </div>
      <div className="line-clamp-2 text-xs">{summary || "No summary"}</div>
      <div className="mt-1 truncate font-mono text-[11px] opacity-60">
        {String(row.artifact_path || row.report_path || row.pattern_key || "")}
      </div>
    </div>
  );
}

function ArtifactUsageCard({
  rows,
  successPathHealth,
  trends,
  attribution,
  recommendations,
  busyKey,
  onRun,
}: {
  rows: ArtifactUsageRow[];
  successPathHealth?: SuccessPathHealth;
  trends: ArtifactUsageTrendRow[];
  attribution?: ArtifactUsageAttribution;
  recommendations: Record<string, unknown>[];
  busyKey?: string | null;
  onRun: (action: GovernanceAction, item: Record<string, unknown>, label: string) => void;
}) {
  const latest = trends.length ? trends[trends.length - 1] : undefined;
  const successSummary = successPathHealth?.summary ?? {};
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
      <div className="mt-3 grid grid-cols-3 gap-2">
        <MiniHealthCard label="Success Paths" value={successSummary.total_paths ?? 0} tone="cyan" />
        <MiniHealthCard label="Reliable" value={successSummary.reliable_count ?? 0} tone="emerald" />
        <MiniHealthCard
          label="Degrading"
          value={successSummary.degraded_count ?? 0}
          tone={(successSummary.degraded_count ?? 0) ? "rose" : "cyan"}
        />
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
        <ArtifactMiniList title="Reliable success paths" rows={successPathHealth?.reliable_paths ?? []} empty="No reliable success path yet" tone="emerald" />
        <ArtifactMiniList title="Degrading success paths" rows={successPathHealth?.degraded_paths ?? []} empty="No degrading success path" tone="rose" />
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
              uses {row.memory_use_count ?? 0} 路 success {Math.round(Number(row.memory_success_rate || 0) * 100)}% 路 failures {row.memory_failure_count ?? 0}
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
            <h2 className="text-sm font-semibold text-cyan-50">浠婃棩娌荤悊寤鸿</h2>
            <p className="mt-1 text-xs text-slate-500">绯荤粺鏍规嵁鍐茬獊銆佸け璐ユā寮忋€侀檲鏃ф蹇靛拰寰呯‘璁ら槦鍒楋紝鎺ㄨ崘鏈€璇ュ鐞嗙殑鐭ヨ瘑浠诲姟銆</p>
          </div>
        </div>
        {onBatch && rows.length ? (
          <button
            className="rounded border border-emerald-300/25 bg-emerald-400/10 px-2 py-1 text-[11px] text-emerald-100 hover:bg-emerald-400/15 disabled:opacity-50"
            onClick={() => onBatch(rows)}
            disabled={Boolean(busyKey)}
            title={`Batch-safe recommendations: ${Math.min(batchableCount, 3)}`}
          >
            {busyKey ? "Running" : "Run batch"}
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
                  {busyKey ? "Running" : "Run"}
                </button>
              </div>
            </div>
          );
        })}
        {rows.length === 0 ? <div className="rounded-md border border-white/10 bg-slate-950/45 p-3 text-xs text-slate-500">鏆傛棤涓诲姩娌荤悊寤鸿</div> : null}
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
          <h2 className="text-sm font-semibold text-cyan-50">娌荤悊鍘嗗彶</h2>
          <p className="mt-1 text-xs text-slate-500">鏈€杩戠殑纭銆佹嫆缁濄€佸綊妗ｃ€侀噸鏂伴獙璇佸拰 playbook 鐢熸垚璁板綍銆</p>
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
        {rows.length === 0 ? <div className="rounded-md border border-white/10 bg-slate-950/45 p-3 text-xs text-slate-500">鏆傛棤娌荤悊鍘嗗彶</div> : null}
      </div>
    </div>
  );
}

function MemoryTrustCard({
  data,
  busyKey,
  onRun,
  onBatch,
}: {
  data?: MemoryTrustSummary;
  busyKey?: string | null;
  onRun?: (
    action: Extract<GovernanceAction, "confirm_memory" | "reject_memory" | "mark_memory_conflicted" | "correct_memory">,
    item: Record<string, unknown>,
    label: string,
  ) => void;
  onBatch?: (
    action: Extract<GovernanceAction, "confirm_memory" | "reject_memory" | "mark_memory_conflicted">,
    rows: MemoryTrustRow[],
    label: string,
  ) => void;
}) {
  const summary = data?.summary ?? {};
  const analytics = data?.analytics;
  const analyticsSummary = analytics?.summary ?? {};
  const blocked = summary.recall_blocked_count ?? 0;
  const [editing, setEditing] = useState<MemoryTrustRow | null>(null);
  const [draft, setDraft] = useState("");
  const rows = data?.review_queue?.length
    ? data.review_queue
    : data?.requires_confirmation?.length
    ? data.requires_confirmation
    : data?.recent_rejected?.length
      ? data.recent_rejected
      : data?.recent_floating?.length
        ? data.recent_floating
        : data?.recent_confirmed ?? [];
  const actionableRows = rows.filter((row) => row.memory_id).slice(0, 10);
  const openCorrection = (row: MemoryTrustRow) => {
    setEditing(row);
    setDraft(row.content || row.content_preview || "");
  };
  const submitCorrection = () => {
    if (!editing?.memory_id || !draft.trim() || !onRun) return;
    onRun(
      "correct_memory",
      { ...(editing as unknown as Record<string, unknown>), corrected_content: draft.trim() },
      "Correct memory trust",
    );
    setEditing(null);
    setDraft("");
  };
  return (
    <div className="mb-4 rounded-lg border border-white/10 bg-white/[0.03] p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-cyan-50">Memory Trust Layer</h2>
          <p className="mt-1 text-xs text-slate-500">Recall now distinguishes confirmed, inferred, rejected, conflicted, and expired memories.</p>
        </div>
        <span className={`rounded border px-2 py-1 text-xs ${blocked ? "border-rose-300/25 bg-rose-400/10 text-rose-100" : "border-emerald-300/25 bg-emerald-400/10 text-emerald-100"}`}>
          blocked {blocked}
        </span>
      </div>
      {onBatch && actionableRows.length ? (
        <div className="mt-3 flex flex-wrap gap-2">
          <MemoryTrustButton
            label={`Confirm ${actionableRows.length}`}
            tone="emerald"
            disabled={Boolean(busyKey)}
            onClick={() => onBatch("confirm_memory", actionableRows, "Batch confirm memory trust")}
          />
          <MemoryTrustButton
            label={`Reject ${actionableRows.length}`}
            tone="rose"
            disabled={Boolean(busyKey)}
            onClick={() => onBatch("reject_memory", actionableRows, "Batch reject memory trust")}
          />
          <MemoryTrustButton
            label={`Conflict ${actionableRows.length}`}
            tone="amber"
            disabled={Boolean(busyKey)}
            onClick={() => onBatch("mark_memory_conflicted", actionableRows, "Batch mark memory conflicted")}
          />
        </div>
      ) : null}
      {editing ? (
        <div className="mt-3 rounded-md border border-cyan-300/20 bg-cyan-400/5 p-3">
          <div className="flex items-center justify-between gap-2">
            <div className="text-xs font-medium text-cyan-50">Correct memory</div>
            <button className="text-xs text-slate-400 hover:text-slate-200" onClick={() => setEditing(null)}>
              Cancel
            </button>
          </div>
          <textarea
            className="mt-2 min-h-24 w-full rounded border border-white/10 bg-slate-950/75 p-2 text-xs text-cyan-50 outline-none focus:border-cyan-300/40"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
          />
          <div className="mt-2 flex items-center justify-end gap-2">
            <MemoryTrustButton label="Save correction" tone="emerald" disabled={Boolean(busyKey) || !draft.trim()} onClick={submitCorrection} />
          </div>
        </div>
      ) : null}
      <div className="mt-3 grid grid-cols-5 gap-2">
        <MiniHealthCard label="confirmed" value={summary.confirmed_count ?? 0} tone="emerald" />
        <MiniHealthCard label="floating" value={summary.floating_count ?? 0} tone="cyan" />
        <MiniHealthCard label="conflict" value={summary.conflicted_count ?? 0} tone={(summary.conflicted_count ?? 0) ? "amber" : "cyan"} />
        <MiniHealthCard label="rejected" value={summary.rejected_count ?? 0} tone={(summary.rejected_count ?? 0) ? "rose" : "cyan"} />
        <MiniHealthCard label="expired" value={summary.expired_count ?? 0} tone="amber" />
      </div>
      {analytics ? (
        <div className="mt-3 rounded-md border border-white/10 bg-slate-950/45 p-3">
          <div className="mb-2 flex items-center justify-between gap-2">
            <div className="text-xs font-medium text-cyan-50">Trust Analytics</div>
            <span className="rounded border border-cyan-300/15 px-1.5 py-0.5 text-[11px] text-cyan-200">
              patterns {analyticsSummary.pattern_count ?? 0}
            </span>
          </div>
          <div className="grid grid-cols-5 gap-2">
            <MiniHealthCard label="bad pattern" value={analyticsSummary.rejected_pattern_count ?? 0} tone={(analyticsSummary.rejected_pattern_count ?? 0) ? "rose" : "cyan"} />
            <MiniHealthCard label="promote" value={analyticsSummary.promotion_candidate_count ?? 0} tone={(analyticsSummary.promotion_candidate_count ?? 0) ? "emerald" : "cyan"} />
            <MiniHealthCard label="clusters" value={analyticsSummary.conflict_cluster_count ?? 0} tone={(analyticsSummary.conflict_cluster_count ?? 0) ? "amber" : "cyan"} />
            <MiniHealthCard label="hotspots" value={analyticsSummary.floating_hotspot_count ?? 0} tone={(analyticsSummary.floating_hotspot_count ?? 0) ? "amber" : "cyan"} />
            <MiniHealthCard label="stale" value={analyticsSummary.stale_confirmed_count ?? 0} tone={(analyticsSummary.stale_confirmed_count ?? 0) ? "amber" : "cyan"} />
          </div>
          <div className="mt-2 grid gap-2 md:grid-cols-2">
            {(analytics.rejected_patterns ?? []).slice(0, 2).map((item, index) => (
              <MemoryTrustAnalyticsRow key={`rejected-${index}`} item={item} tone="rose" label="Stop trusting" />
            ))}
            {(analytics.promotion_candidates ?? []).slice(0, 2).map((item, index) => (
              <MemoryTrustAnalyticsRow key={`promote-${index}`} item={item} tone="emerald" label="Promote" />
            ))}
            {(analytics.stale_confirmed ?? []).slice(0, 2).map((item, index) => (
              <MemoryTrustAnalyticsRow key={`stale-${index}`} item={item} tone="amber" label="Re-check" />
            ))}
          </div>
        </div>
      ) : null}
      <div className="mt-3 space-y-2">
        {rows.slice(0, 8).map((row, index) => (
          <div key={`${row.memory_id || index}-${row.trust_state}`} className="rounded-md border border-white/10 bg-slate-950/45 p-2">
            <div className="flex items-center justify-between gap-2">
              <span className="truncate font-mono text-[11px] text-cyan-100">{row.memory_type || "memory"}</span>
              <div className="flex items-center gap-1.5">
                {typeof row.review_priority === "number" ? (
                  <span className="rounded border border-cyan-300/15 px-1.5 py-0.5 text-[11px] text-cyan-200">p{row.review_priority}</span>
                ) : null}
                <span className="rounded border border-white/10 px-1.5 py-0.5 text-[11px] text-slate-300">{row.trust_state || "floating"}</span>
              </div>
            </div>
            <div className="mt-1 line-clamp-2 text-xs text-slate-400">{row.content_preview || row.trust_reason || "No preview"}</div>
            <div className="mt-1 font-mono text-[10px] text-slate-600">{row.trust_reason || "trust reason unavailable"}</div>
            {onRun && row.memory_id ? (
              <div className="mt-2 flex flex-wrap gap-2">
                {row.trust_state !== "confirmed" ? (
                  <MemoryTrustButton
                    label="Confirm"
                    tone="emerald"
                    disabled={Boolean(busyKey)}
                    onClick={() => onRun("confirm_memory", row as unknown as Record<string, unknown>, "Confirm memory trust")}
                  />
                ) : null}
                {row.trust_state !== "rejected" ? (
                  <MemoryTrustButton
                    label="Reject"
                    tone="rose"
                    disabled={Boolean(busyKey)}
                    onClick={() => onRun("reject_memory", row as unknown as Record<string, unknown>, "Reject memory trust")}
                  />
                ) : null}
                {row.trust_state !== "conflicted" ? (
                  <MemoryTrustButton
                    label="Conflict"
                    tone="amber"
                    disabled={Boolean(busyKey)}
                    onClick={() => onRun("mark_memory_conflicted", row as unknown as Record<string, unknown>, "Mark memory conflicted")}
                  />
                ) : null}
                <MemoryTrustButton label="Correct" tone="emerald" disabled={Boolean(busyKey)} onClick={() => openCorrection(row)} />
              </div>
            ) : null}
          </div>
        ))}
        {!rows.length ? <div className="rounded-md border border-white/10 bg-slate-950/45 p-3 text-xs text-slate-500">No trust review items.</div> : null}
      </div>
    </div>
  );
}

function MemoryTrustAnalyticsRow({ item, tone, label }: { item: Record<string, unknown>; tone: "emerald" | "amber" | "rose"; label: string }) {
  const sample = String(item.sample || item.recommendation || item.pattern_key || "");
  const recommendation = String(item.recommendation || "");
  const toneClass = tone === "emerald"
    ? "border-emerald-300/15 bg-emerald-400/5 text-emerald-100"
    : tone === "rose"
      ? "border-rose-300/15 bg-rose-400/5 text-rose-100"
      : "border-amber-300/15 bg-amber-400/5 text-amber-100";
  return (
    <div className={`rounded border p-2 ${toneClass}`}>
      <div className="mb-1 flex items-center justify-between gap-2">
        <span className="rounded bg-slate-950/50 px-1.5 py-0.5 text-[10px]">{label}</span>
        <span className="text-[10px] opacity-70">{String(item.total_count || item.age_days || "")}</span>
      </div>
      <div className="line-clamp-2 text-xs">{sample || "No sample"}</div>
      {recommendation ? <div className="mt-1 line-clamp-2 text-[11px] opacity-70">{recommendation}</div> : null}
    </div>
  );
}

function MemoryTrustButton({
  label,
  tone,
  disabled,
  onClick,
}: {
  label: string;
  tone: "emerald" | "amber" | "rose";
  disabled?: boolean;
  onClick: () => void;
}) {
  const toneClass =
    tone === "rose"
      ? "border-rose-300/25 bg-rose-400/10 text-rose-100 hover:bg-rose-400/15"
      : tone === "amber"
        ? "border-amber-300/25 bg-amber-400/10 text-amber-100 hover:bg-amber-400/15"
        : "border-emerald-300/25 bg-emerald-400/10 text-emerald-100 hover:bg-emerald-400/15";
  return (
    <button
      className={`rounded border px-2 py-1 text-[11px] transition disabled:opacity-50 ${toneClass}`}
      onClick={onClick}
      disabled={disabled}
    >
      {disabled ? "Working" : label}
    </button>
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
                  {busyKey ? "Running" : actionLabel}
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
                  onClick={() => onGovernance("confirm_pending", row, "纭寰呮矇娣€鐭ヨ瘑")}
                  disabled={Boolean(busyKey)}
                >
                  纭
                </button>
                <button
                  className="rounded border border-rose-300/25 bg-rose-400/10 px-2 py-1 text-[11px] text-rose-100 hover:bg-rose-400/15 disabled:opacity-50"
                  onClick={() => onGovernance("reject_pending", row, "鎷掔粷寰呮矇娣€鐭ヨ瘑")}
                  disabled={Boolean(busyKey)}
                >
                  鎷掔粷
                </button>
                <button
                  className="rounded border border-slate-300/20 bg-slate-400/10 px-2 py-1 text-[11px] text-slate-200 hover:bg-slate-400/15 disabled:opacity-50"
                  onClick={() => onGovernance("defer_pending", row, "绋嶅悗澶勭悊寰呮矇娣€鐭ヨ瘑")}
                  disabled={Boolean(busyKey)}
                >
                  绋嶅悗
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
          <h3 className="text-sm font-semibold text-cyan-50">闄堟棫姒傚康</h3>
          <p className="mt-1 text-xs text-slate-500">瓒呰繃鐢熷懡鍛ㄦ湡鎴栫己灏戣繎鏈熼獙璇佺殑鐭ヨ瘑銆</p>
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
                  onClick={() => onGovernance("revalidate_stale", row, "閲嶆柊楠岃瘉闄堟棫姒傚康")}
                  disabled={Boolean(busyKey)}
                >
                  閲嶆柊楠岃瘉
                </button>
                <button
                  className="rounded border border-amber-300/25 bg-amber-400/10 px-2 py-1 text-[11px] text-amber-100 hover:bg-amber-400/15 disabled:opacity-50"
                  onClick={() => onGovernance("archive_stale", row, "褰掓。闄堟棫姒傚康")}
                  disabled={Boolean(busyKey)}
                >
                  褰掓。
                </button>
              </div>
            ) : null}
          </div>
        ))}
        {rows.length === 0 ? <div className="rounded-md border border-white/10 bg-slate-950/45 p-3 text-xs text-slate-500">鏆傛棤闄堟棫姒傚康</div> : null}
      </div>
    </div>
  );
}
