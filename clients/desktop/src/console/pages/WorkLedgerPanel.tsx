import { useCallback, useEffect, useMemo, useState } from "react";
import { Archive, BookOpenCheck, Bug, CalendarPlus, CheckCircle2, ClipboardList, Copy, FileClock, GitBranch, Inbox, NotebookPen, Play, RefreshCw, Search, Send, Square } from "lucide-react";
import { clearL3SkillsBaseUrlCache, getL3SkillsBaseUrl } from "../../lib/api";
import { WorkLedgerValueLab } from "./WorkLedgerValueLab";

type WorkSession = {
  session_id: string;
  title: string;
  project_name?: string;
  project_path?: string;
  status: "active" | "closed" | string;
  start_time?: string;
  end_time?: string | null;
  user_goal?: string;
  evidence_count?: number;
  output_paths?: Record<string, string>;
};

type WorkEvidence = {
  evidence_id: string;
  source: string;
  collected_at: string;
  summary: string;
  trust_level: string;
  payload?: Record<string, unknown>;
};

type WorkLedgerStatus = {
  ok: boolean;
  home: string;
  active_session?: WorkSession | null;
  recent_sessions: WorkSession[];
  counts: {
    sessions: number;
    active: number;
  };
  project_memory?: WorkLedgerProjectMemory;
};

type WorkLedgerProjectMemoryItem = {
  alias?: string;
  project_name?: string;
  project_path?: string;
  last_session_id?: string;
  last_title?: string;
  last_user_goal?: string;
  updated_at_ms?: number;
  confidence?: number;
  reason?: string;
};

type WorkLedgerProjectMemory = {
  path?: string;
  project_count?: number;
  recent?: WorkLedgerProjectMemoryItem;
  projects?: WorkLedgerProjectMemoryItem[];
};

type WorkLedgerDetail = {
  ok: boolean;
  session: WorkSession;
  evidence: WorkEvidence[];
  paths: Record<string, string>;
  codex_work_chain?: {
    conversation_name?: string;
    request_count: number;
    pending_count: number;
    completed_count: number;
    requests: Array<{
      scenario_id: string;
      request_key: string;
      label: string;
      purpose: string;
      output_use: string;
      phase: string;
      reason: string;
      priority: number;
      status: "pending" | "completed" | string;
    }>;
  };
};

type CodexInvocation = {
  invocation_id: string;
  status: "queued" | "running" | "waiting" | "succeeded" | "failed" | "cancelled" | string;
  stage?: string;
  detail?: string;
  cancel_requested?: boolean;
  created_at?: string;
  updated_at?: string;
  metadata?: {
    session_id?: string;
    request_key?: string;
    project_name?: string;
    conversation_name?: string;
  };
};

type CodexInvocationResponse = {
  ok: boolean;
  active_count: number;
  invocations: CodexInvocation[];
};

type WorkLedgerOutputText = {
  ok: boolean;
  session_id: string;
  output_key: string;
  path: string;
  text: string;
  truncated: boolean;
  char_count: number;
};

type WorkLedgerRecallHit = {
  kind: string;
  session_id?: string;
  title?: string;
  project_name?: string;
  text?: string;
  path?: string;
  score?: number;
  score_parts?: Record<string, number>;
  ranking_reason?: string;
  trust_level?: string;
};

type WorkLedgerRecallResult = {
  ok: boolean;
  query: string;
  window_days: number;
  hit_count: number;
  hits: WorkLedgerRecallHit[];
  index_summary: {
    session_count: number;
    adopted_output_count: number;
    methodology_candidate_count: number;
    verified_outcome_count?: number;
    approved_methodology_count?: number;
    project_counts: Record<string, number>;
  };
};

type WorkLedgerWeeklyResult = {
  ok: boolean;
  path: string;
  text: string;
  days: number;
  session_count: number;
  adopted_output_count: number;
  methodology_candidate_count: number;
  verified_outcome_count?: number;
};

type WorkLedgerBriefResult = {
  ok: boolean;
  path: string;
  source_index_path: string;
  text: string;
  days: number;
  window_mode: "calendar_days" | string;
  session_count: number;
  activity_day_count?: number;
  git_commit_count?: number;
  verified_outcome_count: number;
  changed_file_count: number;
  generated_at?: string;
  baseline_path?: string;
  quality_report_path?: string;
  generation_mode?: "llm_evidence_editor" | "evidence_baseline" | string;
  model?: string;
  codex_consultation?: {
    ok: boolean;
    consulted: boolean;
    reason?: string;
    gap_count?: number;
    success_count?: number;
    reused_count?: number;
    effective_count?: number;
    results?: Array<{
      ok?: boolean;
      deduplicated?: boolean;
      project_name?: string;
      conversation_name?: string;
      tool_detail?: string;
      answer_length?: number;
      completion_state?: {
        status?: string;
        complete?: boolean;
        elapsed_seconds?: number;
      };
      used_in_final_brief?: boolean;
      used_claim_count?: number;
      tool_evidence_path?: string;
      evidence_panel_path?: string;
    }>;
  };
  codex_fusion?: {
    consultation_count?: number;
    successful_reply_count?: number;
    failed_reply_count?: number;
    usable_claim_count?: number;
    available_for_final_synthesis?: boolean;
  };
  fusion_trace?: {
    used_claim_ids?: string[];
    used_interpretation_ids?: string[];
    used_recommendation_ids?: string[];
    ignored_claim_ids?: string[];
  };
  codex_execution?: {
    status?: "fused" | "degraded" | "not_requested" | "not_needed" | string;
    reason?: string;
    requested?: boolean;
    degraded?: boolean;
    wait_budget_seconds?: number;
    waited_seconds?: number;
    verified_reply_count?: number;
    usable_claim_count?: number;
    used_claim_count?: number;
    fallback_strategy?: string;
    assurance?: string;
  };
  codex_execution_path?: string;
  degraded_source?: "current_page_snapshot" | "last_successful_brief" | "empty_snapshot";
  degraded_reason?: string;
  cached_evidence_count?: number;
};

type WorkLedgerEndDayCandidate = {
  kind: string;
  summary?: string;
  count?: number;
  sample?: string[];
  score?: number;
  reason?: string;
  action?: string;
  source?: {
    type?: string;
    file_path?: string;
    root_reason?: string;
    quality_key?: string;
    quality?: WorkLedgerCandidateQualityRow;
    mtime_ms?: number;
    size?: number;
  };
  safety?: {
    ok?: boolean;
    blocked?: boolean;
    types?: string[];
    counts?: Record<string, number>;
  };
};

type WorkLedgerCandidateQualityRow = {
  quality_key: string;
  accepted: number;
  rejected: number;
  blocked: number;
  total: number;
  accept_rate: number;
  score_adjustment: number;
  last_seen_at?: string;
};

type WorkLedgerCandidateQuality = {
  schema_version: number;
  window_days: number;
  generated_at: string;
  sources: Record<string, WorkLedgerCandidateQualityRow>;
  ranked_sources: WorkLedgerCandidateQualityRow[];
  totals: {
    accepted: number;
    rejected: number;
    blocked: number;
    total: number;
  };
  summary: {
    source_count: number;
    positive_sources: number;
    neutral_sources: number;
    negative_sources: number;
  };
};

type WorkLedgerCandidateQualityResponse = {
  ok: boolean;
  quality: WorkLedgerCandidateQuality;
};

type WorkLedgerReliability = {
  schema_version: number;
  window_days: number;
  generated_at: string;
  path?: string;
  metrics: {
    active_days: number;
    current_streak: number;
    session_count: number;
    completion_rate: number;
    asset_formation_rate: number;
    output_adoption_rate: number;
    candidate_adoption_rate: number;
    continuation_hit_rate: number;
    outcome_delivery_rate?: number;
    outcome_adoption_rate?: number;
    outcome_impact_rate?: number;
    continuation_use_rate?: number;
    methodology_reuse_success_rate?: number;
    average_valid_evidence: number;
    overall_score: number;
  };
  daily: Array<{
    date: string;
    session_count: number;
    valid_evidence_count: number;
    health_score: number;
    status: "healthy" | "partial" | "attention" | "idle" | string;
  }>;
  reminders: Array<{
    kind: string;
    severity: string;
    session_id: string;
    title?: string;
    message: string;
  }>;
  recommendations: string[];
};

type WorkLedgerReliabilityResponse = {
  ok: boolean;
  reliability: WorkLedgerReliability;
};

type WorkTimelineEntry = {
  evidence_id: string;
  source: string;
  category: string;
  actor: "user" | "system" | "ai_tool" | string;
  trust_level: string;
  collected_at: string;
  collected_at_ms: number;
  summary: string;
  details?: {
    trigger?: string;
    project_kind?: string;
    changed_file_count?: number;
    recent_file_count?: number;
    action?: string;
    output_key?: string;
    hit?: boolean;
  };
};

type WorkTimeline = {
  schema_version: number;
  session_id: string;
  title?: string;
  status?: string;
  entry_count: number;
  category_counts: Record<string, number>;
  entries: WorkTimelineEntry[];
};

type WorkTimelineResponse = {
  ok: boolean;
  timeline: WorkTimeline;
};

type WorkProcessInboxEvent = {
  event_id: string;
  summary: string;
  excerpt?: string;
  quality_score: number;
  task_association: number;
  source_count: number;
  source_types: string[];
  status: "pending" | "accepted" | "rejected" | "ignored" | "blocked" | string;
  source_chain: Array<{
    source_type: string;
    source_uri: string;
    quality_score?: number;
  }>;
};

type WorkProcessInbox = {
  schema_version: number;
  session_id: string;
  generated_at: string;
  candidate_count: number;
  event_count: number;
  last_refresh?: Record<string, number | string>;
  summary: Record<string, number>;
  events: WorkProcessInboxEvent[];
};

type WorkProcessInboxResponse = {
  ok: boolean;
  inbox: WorkProcessInbox;
};

type WorkProjectFact = {
  fact_id: string;
  canonical_summary: string;
  state: "open" | "completed" | string;
  trust_level: string;
  first_session_id: string;
  first_seen_at: string;
  last_seen_at: string;
  occurrence_count: number;
  source_types: string[];
  session_ids: string[];
  state_version?: number;
  last_state_change_at?: string;
  superseded_by_fact_id?: string;
  supersedes_fact_ids?: string[];
  lifecycle?: Array<{
    transition_id: string;
    from_state?: string | null;
    to_state: string;
    reason?: string;
    session_id?: string;
    observed_at?: string;
  }>;
  decisions?: Array<{ entry_id: string; text: string; observed_at?: string }>;
  failure_attempts?: Array<{ entry_id: string; text: string; observed_at?: string }>;
  next_actions?: Array<{ entry_id: string; text: string; observed_at?: string }>;
};

type WorkProjectFactReview = {
  candidate_id: string;
  new_fact_id: string;
  possible_match_fact_id: string;
  status: string;
  reason: string;
  similarity?: {
    score?: number;
    token_jaccard?: number;
    token_containment?: number;
    artifact_jaccard?: number;
  };
};

type WorkProjectFacts = {
  all_facts: WorkProjectFact[];
  new_facts: WorkProjectFact[];
  continued_facts: WorkProjectFact[];
  prior_open_facts: WorkProjectFact[];
  review_pending: WorkProjectFactReview[];
  summary: {
    fact_count?: number;
    open_count?: number;
    in_progress_count?: number;
    completed_count?: number;
    reopened_count?: number;
    superseded_count?: number;
    review_pending?: number;
  };
};

type WorkProjectFactsResponse = {
  ok: boolean;
  facts: WorkProjectFacts;
};

type WorkVerifiedOutcome = {
  outcome_id: string;
  fact_id: string;
  summary: string;
  status: string;
  completed_at?: string;
  completion_reason?: string;
  completion_session_id?: string;
  verification_evidence_id?: string;
};

type WorkMethodologyCandidate = {
  candidate_id: string;
  fact_id: string;
  title: string;
  trigger: string;
  decision: string;
  action: string;
  result: string;
  status: "pending_review" | "approved" | "rejected" | string;
  review_note?: string;
  evidence_ids?: string[];
};

type WorkProjectOutcomes = {
  active_outcomes: WorkVerifiedOutcome[];
  outcomes_this_session: WorkVerifiedOutcome[];
  methodology_pending: WorkMethodologyCandidate[];
  methodology_approved: WorkMethodologyCandidate[];
  summary: {
    node_count?: number;
    edge_count?: number;
    active_outcome_count?: number;
    historical_outcome_count?: number;
    methodology_pending_count?: number;
    methodology_approved_count?: number;
  };
};

type WorkProjectOutcomesResponse = {
  ok: boolean;
  outcomes: WorkProjectOutcomes;
};

type WorkOutcomeValue = WorkVerifiedOutcome & {
  value_stage: "completed" | "delivered" | "adopted" | "impact" | string;
  value_score: number;
  latest_feedback?: "positive" | "neutral" | "negative" | string;
  feedback_note?: string;
  delivered_count: number;
  adoption_count: number;
  impact_count: number;
  methodology_reuse_count: number;
};

type WorkValueChain = {
  outcome_values_this_session: WorkOutcomeValue[];
  events_this_session: Array<{
    value_event_id: string;
    event_type: string;
    recorded_at: string;
    note?: string;
  }>;
  summary: {
    active_outcome_count?: number;
    delivered_outcome_count?: number;
    adopted_outcome_count?: number;
    impact_outcome_count?: number;
    delivery_rate?: number;
    adoption_rate?: number;
    impact_rate?: number;
    continuation_available_count?: number;
    continuation_used_count?: number;
    continuation_use_rate?: number;
    methodology_reuse_attempt_count?: number;
    methodology_reuse_success_count?: number;
    methodology_reuse_success_rate?: number;
  };
};

type WorkValueChainResponse = {
  ok: boolean;
  value_chain: WorkValueChain;
};

type WorkSourceStatus = {
  schema_version: number;
  session_id: string;
  configured_roots: string[];
  updated_at: string;
  source_count: number;
  paused_count: number;
  error_count: number;
  backoff_count?: number;
  last_refresh: Record<string, number | string>;
  health?: {
    sync_count?: number;
    changed_sync_count?: number;
    unchanged_sync_count?: number;
    failed_source_count?: number;
    total_duration_ms?: number;
    total_bytes?: number;
    total_lines?: number;
    total_events?: number;
    average_duration_ms?: number;
    error_rate?: number;
    backoff_source_count?: number;
  };
  source_profile?: {
    project_key?: string;
    project_path?: string;
    inherited?: boolean;
    inherited_from_session_id?: string;
    profile_updated_at_ms?: number;
  };
  authorizations?: Array<{
    path: string;
    exists: boolean;
    readable: boolean;
    source_count: number;
    total_line_count: number;
    last_sync_at?: string;
  }>;
  sources: Array<{
    source_key: string;
    source_uri: string;
    source_type?: string;
    status?: string;
    paused?: boolean;
    last_sync_at?: string;
    last_error?: string;
    consecutive_errors?: number;
    backoff_seconds?: number;
    backoff_until_ms?: number;
    total_read_count?: number;
    total_line_count?: number;
  }>;
};

type WorkSourceStatusResponse = {
  ok: boolean;
  status: WorkSourceStatus;
};

type WorkLedgerEndDayPreview = {
  ok: boolean;
  preview: {
    session_id: string;
    title?: string;
    candidates: WorkLedgerEndDayCandidate[];
    safety?: {
      ok?: boolean;
      blocked?: boolean;
      types?: string[];
      counts?: Record<string, number>;
    };
    recommended_outputs?: string[];
    candidate_quality?: WorkLedgerCandidateQuality;
  };
  session: WorkSession;
  evidence: WorkEvidence[];
  paths: Record<string, string>;
};

const WORK_LEDGER_L3_RETRY_DELAYS_MS = [0, 500, 1_200, 2_400] as const;
const WORK_LEDGER_BRIEF_CACHE_KEY = "jachin.workLedger.lastSuccessfulInstantBrief.v1";

async function resolveWorkLedgerL3Base(): Promise<string> {
  let lastError: unknown = null;

  for (let attempt = 0; attempt < WORK_LEDGER_L3_RETRY_DELAYS_MS.length; attempt += 1) {
    const delayMs = WORK_LEDGER_L3_RETRY_DELAYS_MS[attempt];
    if (delayMs > 0) {
      await new Promise<void>((resolve) => window.setTimeout(resolve, delayMs));
    }
    if (attempt > 0) {
      clearL3SkillsBaseUrlCache();
    }
    try {
      return await getL3SkillsBaseUrl({ bypassCache: attempt > 0 });
    } catch (error) {
      lastError = error;
    }
  }

  const reason = lastError instanceof Error ? lastError.message : String(lastError || "unknown");
  throw new Error(`L3 正在启动或尚未就绪，已自动重试 ${WORK_LEDGER_L3_RETRY_DELAYS_MS.length} 次。最后错误：${reason}`);
}

async function callWorkLedger<T>(path: string, init?: RequestInit): Promise<T> {
  const base = await resolveWorkLedgerL3Base();
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

function sourceLabel(source: string) {
  if (source === "git_snapshot") return "Git";
  if (source === "file_scan") return "文件";
  if (source === "manual_note") return "手动记录";
  if (source === "work_output") return "输出";
  if (source === "work_output_adoption") return "采纳回流";
  if (source === "work_value_event") return "价值回流";
  if (source === "work_session") return "任务";
  return source;
}

function compactBriefText(value: unknown, maxLength = 500) {
  const text = String(value ?? "").replace(/\s+/g, " ").trim();
  return text.length > maxLength ? `${text.slice(0, maxLength - 1)}…` : text;
}

function formatBriefUtc8Time(value: string | number | Date = new Date()) {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return compactBriefText(value, 80) || "未知";
  const shifted = new Date(date.getTime() + 8 * 60 * 60 * 1000);
  return `${shifted.toISOString().slice(0, -1)}+08:00`;
}

function readCachedInstantBrief(): WorkLedgerBriefResult | null {
  try {
    const raw = window.localStorage.getItem(WORK_LEDGER_BRIEF_CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<WorkLedgerBriefResult>;
    if (!parsed.ok || typeof parsed.text !== "string" || !parsed.text.trim()) return null;
    return parsed as WorkLedgerBriefResult;
  } catch {
    return null;
  }
}

function cacheSuccessfulInstantBrief(payload: WorkLedgerBriefResult) {
  try {
    const cached: WorkLedgerBriefResult = {
      ok: true,
      path: payload.path,
      source_index_path: payload.source_index_path,
      text: payload.text,
      days: payload.days,
      window_mode: payload.window_mode,
      session_count: payload.session_count,
      activity_day_count: payload.activity_day_count,
      git_commit_count: payload.git_commit_count,
      verified_outcome_count: payload.verified_outcome_count,
      changed_file_count: payload.changed_file_count,
      generated_at: payload.generated_at,
      generation_mode: payload.generation_mode,
      model: payload.model,
      cached_evidence_count: payload.changed_file_count,
    };
    window.localStorage.setItem(WORK_LEDGER_BRIEF_CACHE_KEY, JSON.stringify(cached));
  } catch {
    // 缓存不可用不应影响已经成功生成的简报。
  }
}

function buildDegradedInstantBrief(options: {
  days: number;
  status: WorkLedgerStatus | null;
  detail: WorkLedgerDetail | null;
  cachedBrief: WorkLedgerBriefResult | null;
  reason: unknown;
}): WorkLedgerBriefResult {
  const { days, status, detail, cachedBrief, reason } = options;
  const errorReason = compactBriefText(reason, 240) || "unknown";
  const generatedAt = formatBriefUtc8Time();
  const evidence = detail?.evidence ?? [];
  const visibleSessions = detail?.session
    ? [detail.session]
    : [
        ...(status?.active_session ? [status.active_session] : []),
        ...(status?.recent_sessions ?? []),
      ].filter(
        (session, index, sessions) =>
          sessions.findIndex((item) => item.session_id === session.session_id) === index,
      ).slice(0, 5);
  const workChain = detail?.codex_work_chain;
  const hasCurrentSnapshot = visibleSessions.length > 0 || evidence.length > 0 || Boolean(workChain);
  const degradedSource: WorkLedgerBriefResult["degraded_source"] = hasCurrentSnapshot
    ? "current_page_snapshot"
    : cachedBrief
      ? "last_successful_brief"
      : "empty_snapshot";
  const activityDays = new Set(
    evidence
      .map((item) => String(item.collected_at || "").slice(0, 10))
      .filter(Boolean),
  ).size;

  let text: string;
  if (hasCurrentSnapshot) {
    const lines = [
      `# ${days === 1 ? "今日" : `最近 ${days} 天`}工作简报（离线降级版）`,
      "",
      "> L3 简报接口当前不可用，未等待或采用 Codex 回复。以下内容只来自当前页面已加载的工作账本快照，不代表生成时刻的最新磁盘状态。",
      "",
      "## 一、当前页面可见工作",
      "",
    ];
    if (visibleSessions.length) {
      visibleSessions.forEach((session, index) => {
        const project = compactBriefText(session.project_name || session.project_path || "未标注项目", 180);
        const goal = compactBriefText(session.user_goal, 280);
        lines.push(
          `${index + 1}. ${compactBriefText(session.title || session.session_id, 180)}（项目：${project}；状态：${session.status || "未知"}${goal ? `；目标：${goal}` : ""}）。`,
        );
      });
    } else {
      lines.push("1. 当前页面没有已加载的任务记录，无法确认正在进行或已经完成的工作。");
    }

    lines.push("", "## 二、当前页面可见证据", "");
    const visibleEvidence = [...evidence].slice(-12).reverse();
    if (visibleEvidence.length) {
      visibleEvidence.forEach((item, index) => {
        lines.push(
          `${index + 1}. [${sourceLabel(item.source)}] ${compactBriefText(item.summary, 500) || "该证据没有摘要"}（采集时间：${formatBriefUtc8Time(item.collected_at)}）。`,
        );
      });
    } else {
      lines.push("1. 当前页面没有可复用的 Git、文件或人工记录证据，因此本降级版不声明任何完成成果。");
    }

    lines.push("", "## 三、Codex 工作计划协作", "");
    if (workChain) {
      lines.push(
        `1. 当前页面记录了 ${workChain.request_count ?? 0} 个协作项，其中 ${workChain.completed_count ?? 0} 个已完成、${workChain.pending_count ?? 0} 个待处理。`,
      );
      (workChain.requests ?? []).slice(0, 8).forEach((item, index) => {
        lines.push(
          `${index + 2}. ${compactBriefText(item.label, 180)}（状态：${item.status === "completed" ? "已完成" : "待处理"}；用途：${compactBriefText(item.output_use || item.purpose, 300) || "未标注"}）。`,
        );
      });
    } else {
      lines.push("1. 当前页面没有已加载的 Codex 协作结果；本次降级简报未使用任何 Codex 内容。");
    }

    lines.push(
      "",
      "## 四、风险与下一步",
      "",
      "1. L3 不可达期间无法刷新 Git、文件和工作账本证据，页面快照之后发生的变化没有进入本简报。",
      "2. L3 恢复后应重新生成一次正式简报，以补采最新证据并按设置决定是否等待 Codex。",
    );
    text = lines.join("\n");
  } else if (cachedBrief) {
    text = [
      `# ${days === 1 ? "今日" : `最近 ${days} 天`}工作简报（上次成功结果降级版）`,
      "",
      `> L3 简报接口当前不可用，且当前页面没有已加载证据。下面复用上次成功生成的简报（原生成时间：${cachedBrief.generated_at ? formatBriefUtc8Time(cachedBrief.generated_at) : "未知"}），不代表当前最新状态。`,
      "",
      cachedBrief.text.trim(),
    ].join("\n");
  } else {
    text = [
      `# ${days === 1 ? "今日" : `最近 ${days} 天`}工作简报（离线降级版）`,
      "",
      "> L3 简报接口当前不可用，当前页面也没有已加载或上次成功缓存的工作证据。",
      "",
      "## 可确认信息",
      "",
      "1. 本次没有取得可用于汇报的项目证据，因此不声明任何已完成工作。",
      "",
      "## 下一步",
      "",
      "1. 启动或恢复 L3 后重新生成简报，以采集真实 Git、文件和工作账本证据。",
    ].join("\n");
  }

  return {
    ok: true,
    path: degradedSource === "last_successful_brief" ? cachedBrief?.path || "" : "",
    source_index_path:
      degradedSource === "last_successful_brief" ? cachedBrief?.source_index_path || "" : "",
    text,
    days,
    window_mode: "calendar_days",
    session_count:
      degradedSource === "last_successful_brief"
        ? cachedBrief?.session_count ?? 0
        : visibleSessions.length,
    activity_day_count:
      degradedSource === "last_successful_brief"
        ? cachedBrief?.activity_day_count ?? 0
        : activityDays,
    git_commit_count:
      degradedSource === "last_successful_brief"
        ? cachedBrief?.git_commit_count ?? 0
        : 0,
    verified_outcome_count:
      degradedSource === "last_successful_brief"
        ? cachedBrief?.verified_outcome_count ?? 0
        : 0,
    changed_file_count:
      degradedSource === "last_successful_brief"
        ? cachedBrief?.changed_file_count ?? 0
        : 0,
    generated_at: generatedAt,
    generation_mode: "client_degraded_evidence_baseline",
    codex_consultation: {
      ok: false,
      consulted: false,
      reason: "l3_briefing_unavailable",
      results: [],
    },
    codex_execution: {
      status: "degraded",
      reason: "l3_briefing_unavailable",
      requested: false,
      degraded: true,
      verified_reply_count: 0,
      usable_claim_count: 0,
      used_claim_count: 0,
      fallback_strategy:
        degradedSource === "last_successful_brief"
          ? "last_successful_brief"
          : "client_cached_work_ledger_snapshot",
      assurance: "no unverified Codex content entered the degraded brief",
    },
    degraded_source: degradedSource,
    degraded_reason: errorReason,
    cached_evidence_count:
      degradedSource === "last_successful_brief"
        ? cachedBrief?.cached_evidence_count ?? cachedBrief?.changed_file_count ?? 0
        : evidence.length,
  };
}

function projectFactStateLabel(state: string) {
  if (state === "in_progress") return "进行中";
  if (state === "completed") return "已完成";
  if (state === "reopened") return "已重开";
  if (state === "superseded") return "已替代";
  return "未闭环";
}

function projectFactStateClass(state: string) {
  if (state === "completed") return "text-emerald-200";
  if (state === "in_progress") return "text-cyan-200";
  if (state === "superseded") return "text-slate-500";
  return "text-amber-200";
}

function valueStageLabel(stage: string) {
  if (stage === "impact") return "已产生影响";
  if (stage === "adopted") return "已采用";
  if (stage === "delivered") return "已交付";
  return "已完成";
}

function sourceIcon(source: string) {
  if (source === "git_snapshot") return GitBranch;
  if (source === "file_scan") return FileClock;
  if (source === "manual_note") return NotebookPen;
  return BookOpenCheck;
}

function basename(path?: string) {
  if (!path) return "";
  return path.replace(/\\/g, "/").split("/").filter(Boolean).pop() || path;
}

function candidateSourceLabel(source: string) {
  const labels: Record<string, string> = {
    codex_trace: "Codex 过程",
    cursor_trace: "Cursor 过程",
    terminal_log: "终端日志",
    jachin_runtime_log: "Jachin 运行日志",
    work_ledger_output: "工作账本输出",
    project_markdown: "项目 Markdown",
    structured_log: "结构化日志",
  };
  return labels[source] || source.replace(/_/g, " ");
}

function timelineCategoryLabel(category: string) {
  const labels: Record<string, string> = {
    task: "任务",
    continuation: "续接",
    user_note: "用户记录",
    ai_process: "AI 过程",
    checkpoint: "检查点",
    system_observation: "系统观察",
    candidate_feedback: "候选反馈",
    preview: "收工预览",
    output: "工作输出",
    adoption: "采纳回流",
    value: "成果价值",
  };
  return labels[category] || "其他";
}

function timelineActorLabel(actor: string) {
  if (actor === "user") return "用户确认";
  if (actor === "ai_tool") return "AI 过程导入";
  return "系统观察";
}

function WorkLedgerWorkspace() {
  const [status, setStatus] = useState<WorkLedgerStatus | null>(null);
  const [detail, setDetail] = useState<WorkLedgerDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [projectPath, setProjectPath] = useState("D:\\Projects\\jachi\\jachin-system-main");
  const [goal, setGoal] = useState("");
  const [note, setNote] = useState("");
  const [aiTraceText, setAiTraceText] = useState("");
  const [aiTracePath, setAiTracePath] = useState("");
  const [larkBriefPreview, setLarkBriefPreview] = useState<string | null>(null);
  const [workReportPreview, setWorkReportPreview] = useState<string | null>(null);
  const [briefDays, setBriefDays] = useState(1);
  const [consultCodexForBrief, setConsultCodexForBrief] = useState(false);
  const [codexBriefWaitSeconds, setCodexBriefWaitSeconds] = useState(300);
  const [briefPreview, setBriefPreview] = useState<WorkLedgerBriefResult | null>(null);
  const [recallQuery, setRecallQuery] = useState("");
  const [recallDays, setRecallDays] = useState(14);
  const [recallResult, setRecallResult] = useState<WorkLedgerRecallResult | null>(null);
  const [weeklyPreview, setWeeklyPreview] = useState<WorkLedgerWeeklyResult | null>(null);
  const [endDayPreview, setEndDayPreview] = useState<WorkLedgerEndDayPreview["preview"] | null>(null);
  const [candidateQuality, setCandidateQuality] = useState<WorkLedgerCandidateQuality | null>(null);
  const [reliability, setReliability] = useState<WorkLedgerReliability | null>(null);
  const [timeline, setTimeline] = useState<WorkTimeline | null>(null);
  const [processInbox, setProcessInbox] = useState<WorkProcessInbox | null>(null);
  const [projectFacts, setProjectFacts] = useState<WorkProjectFacts | null>(null);
  const [projectOutcomes, setProjectOutcomes] = useState<WorkProjectOutcomes | null>(null);
  const [valueChain, setValueChain] = useState<WorkValueChain | null>(null);
  const [sourceStatus, setSourceStatus] = useState<WorkSourceStatus | null>(null);
  const [sourceRoots, setSourceRoots] = useState("");
  const [codexInvocations, setCodexInvocations] = useState<CodexInvocation[]>([]);
  const [codexCancelBusy, setCodexCancelBusy] = useState<string | null>(null);
  const active = status?.active_session ?? null;
  const projectMemory = status?.project_memory;
  const rememberedProjects = projectMemory?.projects ?? [];

  const load = useCallback(async (sessionId?: string) => {
    setLoading(true);
    try {
      const [next, qualityPayload, reliabilityPayload] = await Promise.all([
        callWorkLedger<WorkLedgerStatus>("/api/v1/work-ledger/status"),
        callWorkLedger<WorkLedgerCandidateQualityResponse>("/api/v1/work-ledger/candidate-quality", {
          method: "POST",
          body: JSON.stringify({ days: 30 }),
        }).catch(() => null),
        callWorkLedger<WorkLedgerReliabilityResponse>("/api/v1/work-ledger/reliability?days=7").catch(() => null),
      ]);
      setStatus(next);
      if (qualityPayload?.quality) setCandidateQuality(qualityPayload.quality);
      if (reliabilityPayload?.reliability) setReliability(reliabilityPayload.reliability);
      const sid = sessionId || next.active_session?.session_id || detail?.session.session_id || next.recent_sessions?.[0]?.session_id;
      if (sid) {
        const [nextDetail, timelinePayload, inboxPayload, sourceStatusPayload, factsPayload, outcomesPayload, valuePayload] = await Promise.all([
          callWorkLedger<WorkLedgerDetail>(`/api/v1/work-ledger/sessions/${encodeURIComponent(sid)}`),
          callWorkLedger<WorkTimelineResponse>(`/api/v1/work-ledger/sessions/${encodeURIComponent(sid)}/timeline?limit=300`).catch(() => null),
          callWorkLedger<WorkProcessInboxResponse>(`/api/v1/work-ledger/sessions/${encodeURIComponent(sid)}/process-inbox`).catch(() => null),
          callWorkLedger<WorkSourceStatusResponse>(`/api/v1/work-ledger/sessions/${encodeURIComponent(sid)}/source-status`).catch(() => null),
          callWorkLedger<WorkProjectFactsResponse>(`/api/v1/work-ledger/sessions/${encodeURIComponent(sid)}/project-facts`).catch(() => null),
          callWorkLedger<WorkProjectOutcomesResponse>(`/api/v1/work-ledger/sessions/${encodeURIComponent(sid)}/project-outcomes`).catch(() => null),
          callWorkLedger<WorkValueChainResponse>(`/api/v1/work-ledger/sessions/${encodeURIComponent(sid)}/value-chain`).catch(() => null),
        ]);
        setDetail(nextDetail);
        setTimeline(timelinePayload?.timeline || null);
        setProcessInbox(inboxPayload?.inbox || null);
        setSourceStatus(sourceStatusPayload?.status || null);
        setProjectFacts(factsPayload?.facts || null);
        setProjectOutcomes(outcomesPayload?.outcomes || null);
        setValueChain(valuePayload?.value_chain || null);
        setSourceRoots(sourceStatusPayload?.status?.configured_roots?.join("\n") || "");
      } else {
        setDetail(null);
        setTimeline(null);
        setProcessInbox(null);
        setSourceStatus(null);
        setProjectFacts(null);
        setProjectOutcomes(null);
        setValueChain(null);
        setSourceRoots("");
      }
      setNotice(null);
    } catch (e) {
      setNotice(`工作账本加载失败：${String(e)}`);
    } finally {
      setLoading(false);
    }
  }, [detail?.session.session_id]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const sid = detail?.session.session_id;
    if (!sid || detail?.session.status !== "active") return undefined;
    const poll = async () => {
      const [inboxPayload, sourceStatusPayload, factsPayload, outcomesPayload, valuePayload] = await Promise.all([
        callWorkLedger<WorkProcessInboxResponse>(
          `/api/v1/work-ledger/sessions/${encodeURIComponent(sid)}/process-inbox`,
        ).catch(() => null),
        callWorkLedger<WorkSourceStatusResponse>(
          `/api/v1/work-ledger/sessions/${encodeURIComponent(sid)}/source-status`,
        ).catch(() => null),
        callWorkLedger<WorkProjectFactsResponse>(
          `/api/v1/work-ledger/sessions/${encodeURIComponent(sid)}/project-facts`,
        ).catch(() => null),
        callWorkLedger<WorkProjectOutcomesResponse>(
          `/api/v1/work-ledger/sessions/${encodeURIComponent(sid)}/project-outcomes`,
        ).catch(() => null),
        callWorkLedger<WorkValueChainResponse>(
          `/api/v1/work-ledger/sessions/${encodeURIComponent(sid)}/value-chain`,
        ).catch(() => null),
      ]);
      if (inboxPayload?.inbox) setProcessInbox(inboxPayload.inbox);
      if (sourceStatusPayload?.status) setSourceStatus(sourceStatusPayload.status);
      if (factsPayload?.facts) setProjectFacts(factsPayload.facts);
      if (outcomesPayload?.outcomes) setProjectOutcomes(outcomesPayload.outcomes);
      if (valuePayload?.value_chain) setValueChain(valuePayload.value_chain);
    };
    const timer = window.setInterval(() => void poll(), 15_000);
    return () => window.clearInterval(timer);
  }, [detail?.session.session_id, detail?.session.status]);

  useEffect(() => {
    const sid = detail?.session.session_id;
    if (!sid) {
      setCodexInvocations([]);
      return undefined;
    }
    let disposed = false;
    const poll = async () => {
      const payload = await callWorkLedger<CodexInvocationResponse>(
        `/api/v1/work-ledger/codex-invocations?session_id=${encodeURIComponent(sid)}&limit=20`,
      ).catch(() => null);
      if (!disposed && payload?.invocations) {
        setCodexInvocations(payload.invocations);
      }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 2_000);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [detail?.session.session_id]);

  const evidence = detail?.evidence ?? [];
  const latestGit = useMemo(() => [...evidence].reverse().find((item) => item.source === "git_snapshot"), [evidence]);
  const latestFiles = useMemo(() => [...evidence].reverse().find((item) => item.source === "file_scan"), [evidence]);
  const manualNotes = useMemo(() => evidence.filter((item) => item.source === "manual_note"), [evidence]);
  const visibleProjectFacts = useMemo(
    () => [
      ...(projectFacts?.all_facts || []),
      ...(projectFacts?.new_facts || []),
      ...(projectFacts?.continued_facts || []),
      ...(projectFacts?.prior_open_facts || []),
    ].filter((fact, index, values) => values.findIndex((item) => item.fact_id === fact.fact_id) === index),
    [projectFacts],
  );
  const projectFactById = useMemo(
    () => new Map(visibleProjectFacts.map((fact) => [fact.fact_id, fact])),
    [visibleProjectFacts],
  );

  const run = async (label: string, action: () => Promise<string | undefined>) => {
    setBusy(label);
    setNotice(null);
    try {
      const sid = await action();
      await load(sid);
    } catch (e) {
      setNotice(`${label}失败：${String(e)}`);
    } finally {
      setBusy(null);
    }
  };

  const startToday = () =>
    run("开始今天工作", async () => {
      const today = new Date().toISOString().slice(0, 10);
      const payload = await callWorkLedger<WorkLedgerDetail>("/api/v1/work-ledger/start", {
        method: "POST",
        body: JSON.stringify({
          title: `${today} 工作记录`,
          project_path: projectPath.trim(),
          user_goal: goal.trim() || `${today} 工作记录`,
          created_from: "console_daily_loop",
          auto_collect: true,
        }),
      });
      setTitle("");
      setGoal("");
      setLarkBriefPreview(null);
      setWorkReportPreview(null);
      setNotice("已开始今天工作，并完成初始 Git / 文件证据采集。");
      return payload.session.session_id;
    });

  const startTask = () =>
    run("开始任务", async () => {
      const payload = await callWorkLedger<WorkLedgerDetail>("/api/v1/work-ledger/start", {
        method: "POST",
        body: JSON.stringify({
          title: title.trim(),
          project_path: projectPath.trim(),
          user_goal: goal.trim() || title.trim(),
          created_from: "console",
          auto_collect: true,
        }),
      });
      setTitle("");
      setGoal("");
      setLarkBriefPreview(null);
      setWorkReportPreview(null);
      setNotice("已开始任务，并完成初始 Git / 文件证据采集。");
      return payload.session.session_id;
    });

  const copyLarkBrief = () =>
    run("复制 Lark 短版", async () => {
      const sid = detail?.session.session_id || active?.session_id;
      if (!sid) throw new Error("no session selected");
      await callWorkLedger("/api/v1/work-ledger/generate", {
        method: "POST",
        body: JSON.stringify({ session_id: sid }),
      });
      const output = await callWorkLedger<WorkLedgerOutputText>(
        `/api/v1/work-ledger/sessions/${encodeURIComponent(sid)}/outputs/lark_brief?max_chars=1200`,
      );
      const text = output.text.trim();
      setLarkBriefPreview(text);
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
        setNotice("Lark 短版已复制到剪贴板。");
      } else {
        setNotice("已读取 Lark 短版；当前环境不支持自动复制，请在预览区手动复制。");
      }
      return sid;
    });

  const copyWorkReport = () =>
    run("复制逐条工作汇报", async () => {
      const sid = detail?.session.session_id || active?.session_id;
      if (!sid) throw new Error("no session selected");
      await callWorkLedger("/api/v1/work-ledger/generate", {
        method: "POST",
        body: JSON.stringify({ session_id: sid }),
      });
      const output = await callWorkLedger<WorkLedgerOutputText>(
        `/api/v1/work-ledger/sessions/${encodeURIComponent(sid)}/outputs/work_report_summary?max_chars=12000`,
      );
      const text = output.text.trim();
      setWorkReportPreview(text);
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
        setNotice("逐条工作汇报已复制到剪贴板。");
      } else {
        setNotice("逐条工作汇报已生成；当前环境不支持自动复制，请在预览区手动复制。");
      }
      return sid;
    });

  const collect = () =>
    run("采集证据", async () => {
      await callWorkLedger("/api/v1/work-ledger/collect", {
        method: "POST",
        body: JSON.stringify({ session_id: active?.session_id, trigger: "console_manual" }),
      });
      setNotice("已采集最新 Git / 文件证据。");
      return active?.session_id;
    });

  const checkpoint = () =>
    run("记录检查点", async () => {
      if (!active?.session_id) throw new Error("no active session");
      const payload = await callWorkLedger<{ ok: boolean; deduplicated?: boolean }>("/api/v1/work-ledger/checkpoint", {
        method: "POST",
        body: JSON.stringify({ session_id: active.session_id, trigger: "console_manual", force: false }),
      });
      setNotice(payload.deduplicated ? "当前工作状态没有变化，已跳过重复检查点。" : "已记录新的轻量工作检查点。");
      return active.session_id;
    });

  const addNote = () =>
    run("补充记录", async () => {
      await callWorkLedger("/api/v1/work-ledger/note", {
        method: "POST",
        body: JSON.stringify({ session_id: active?.session_id, text: note.trim() }),
      });
      setNote("");
      setNotice("已写入用户确认的过程记录。");
      return active?.session_id;
    });

  const importAiTraceFromClipboard = () =>
    run("导入 AI 过程", async () => {
      if (!active?.session_id) throw new Error("no active session");
      if (!navigator.clipboard?.readText) throw new Error("clipboard read is unavailable");
      const text = (await navigator.clipboard.readText()).trim();
      if (!text) throw new Error("clipboard is empty");
      const toolName = /cursor/i.test(text) ? "Cursor" : /codex/i.test(text) ? "Codex" : "AI";
      await callWorkLedger("/api/v1/work-ledger/import-process", {
        method: "POST",
        body: JSON.stringify({
          session_id: active.session_id,
          text,
          tool_name: toolName,
          trace_kind: "clipboard_import",
          auto_collect: true,
          generate_outputs: true,
        }),
      });
      setNotice(`已从剪贴板导入 ${toolName} 过程记录。`);
      return active.session_id;
    });

  const importAiTraceText = () =>
    run("导入 AI 工作过程", async () => {
      if (!active?.session_id) throw new Error("no active session");
      const text = aiTraceText.trim();
      if (!text) throw new Error("process text is empty");
      const toolName = /cursor/i.test(text) ? "Cursor" : /codex/i.test(text) ? "Codex" : "AI";
      await callWorkLedger("/api/v1/work-ledger/import-process", {
        method: "POST",
        body: JSON.stringify({
          session_id: active.session_id,
          text,
          tool_name: toolName,
          trace_kind: "console_text_import",
          auto_collect: true,
          generate_outputs: true,
        }),
      });
      setAiTraceText("");
      setNotice(`已导入 ${toolName} 工作过程，并刷新 Context Pack / 日报 / 简报。`);
      return active.session_id;
    });

  const importAiTraceFile = () =>
    run("导入日志文件", async () => {
      if (!active?.session_id) throw new Error("no active session");
      const filePath = aiTracePath.trim();
      if (!filePath) throw new Error("log file path is empty");
      await callWorkLedger("/api/v1/work-ledger/import-process", {
        method: "POST",
        body: JSON.stringify({
          session_id: active.session_id,
          file_path: filePath,
          tool_name: "Terminal",
          trace_kind: "console_log_file_import",
          auto_collect: true,
          generate_outputs: true,
        }),
      });
      setNotice("已导入日志文件，并刷新 Context Pack / 日报 / 简报。");
      return active.session_id;
    });

  const generate = () =>
    run("生成输出", async () => {
      await callWorkLedger("/api/v1/work-ledger/generate", {
        method: "POST",
        body: JSON.stringify({ session_id: detail?.session.session_id || active?.session_id }),
      });
      setNotice("已生成日报和 Codex/Cursor 续写 Prompt。");
      return detail?.session.session_id || active?.session_id;
    });

  const previewEndDay = () =>
    run("收工预览", async () => {
      if (!active?.session_id) throw new Error("no active session");
      const payload = await callWorkLedger<WorkLedgerEndDayPreview>("/api/v1/work-ledger/end-day-preview", {
        method: "POST",
        body: JSON.stringify({
          session_id: active.session_id,
          process_text: aiTraceText.trim(),
          process_file_path: aiTracePath.trim(),
        }),
      });
      setEndDayPreview(payload.preview);
      setCandidateQuality(payload.preview.candidate_quality || null);
      setNotice("已生成收工预览，确认后会刷新日报、上下文包和 Lark 简报。");
      return active.session_id;
    });

  const finalizeEndDay = () =>
    run("确认收工", async () => {
      if (!active?.session_id) throw new Error("no active session");
      await callWorkLedger("/api/v1/work-ledger/end-day-finalize", {
        method: "POST",
        body: JSON.stringify({
          session_id: active.session_id,
          process_text: aiTraceText.trim(),
          process_file_path: aiTracePath.trim(),
          close_session: true,
        }),
      });
      setAiTraceText("");
      setEndDayPreview(null);
      setNotice("已确认收工，日报、复盘、上下文包和 Lark 简报已生成。");
      return active.session_id;
    });

  const adoptOutput = (outputKey: string) =>
    run("采纳回流", async () => {
      const sid = detail?.session.session_id || active?.session_id;
      if (!sid) throw new Error("no session selected");
      await callWorkLedger("/api/v1/work-ledger/adopt-output", {
        method: "POST",
        body: JSON.stringify({
          session_id: sid,
          output_key: outputKey,
          adopted_by: "console",
          note: "用户在 Work Ledger 控制台采纳该输出，用于后续复盘和知识沉淀。",
        }),
      });
      setNotice("已采纳该输出，并回流到 AI 自生长知识系统。");
      return sid;
    });

  const adoptCandidate = (filePath: string) =>
    run("采纳候选", async () => {
      const sid = detail?.session.session_id || active?.session_id;
      if (!sid) throw new Error("no session selected");
      if (!filePath.trim()) throw new Error("candidate file path is required");
      await callWorkLedger("/api/v1/work-ledger/adopt-candidate", {
        method: "POST",
        body: JSON.stringify({
          session_id: sid,
          file_path: filePath.trim(),
          adopted_by: "console",
          note: "用户在 Work Ledger 收工预览中采纳了这份自动发现的过程材料。",
          generate_outputs_after: true,
        }),
      });
      const refreshed = await callWorkLedger<WorkLedgerEndDayPreview>("/api/v1/work-ledger/end-day-preview", {
        method: "POST",
        body: JSON.stringify({ session_id: sid }),
      });
      setEndDayPreview(refreshed.preview);
      setCandidateQuality(refreshed.preview.candidate_quality || null);
      setAiTracePath(filePath.trim());
      setNotice("已采纳候选材料，来源质量和候选排序已刷新。");
      return sid;
    });

  const rejectCandidate = (filePath: string) =>
    run("拒绝候选", async () => {
      const sid = detail?.session.session_id || active?.session_id;
      if (!sid) throw new Error("no session selected");
      if (!filePath.trim()) throw new Error("candidate file path is required");
      await callWorkLedger("/api/v1/work-ledger/reject-candidate", {
        method: "POST",
        body: JSON.stringify({
          session_id: sid,
          file_path: filePath.trim(),
          note: "用户在 Work Ledger 收工预览中拒绝了这份自动发现的过程材料。",
        }),
      });
      const refreshed = await callWorkLedger<WorkLedgerEndDayPreview>("/api/v1/work-ledger/end-day-preview", {
        method: "POST",
        body: JSON.stringify({ session_id: sid }),
      });
      setEndDayPreview(refreshed.preview);
      setCandidateQuality(refreshed.preview.candidate_quality || null);
      setNotice("已拒绝候选，来源质量和候选排序已刷新。");
      return sid;
    });

  const recall = () =>
    run("召回工作记忆", async () => {
      if (!recallQuery.trim()) throw new Error("recall query is required");
      const payload = await callWorkLedger<WorkLedgerRecallResult>("/api/v1/work-ledger/recall", {
        method: "POST",
        body: JSON.stringify({ query: recallQuery.trim(), days: recallDays, limit: 8 }),
      });
      setRecallResult(payload);
      setNotice(`已从最近 ${payload.window_days} 天召回 ${payload.hit_count} 条相关工作记忆。`);
      return detail?.session.session_id || active?.session_id;
    });

  const generateMultiDayWeekly = () =>
    run("生成多日周报", async () => {
      const payload = await callWorkLedger<WorkLedgerWeeklyResult>("/api/v1/work-ledger/weekly-report", {
        method: "POST",
        body: JSON.stringify({ days: recallDays }),
      });
      setWeeklyPreview(payload);
      setNotice(`已生成最近 ${payload.days} 天工作周报，聚合 ${payload.session_count} 个任务。`);
      return detail?.session.session_id || active?.session_id;
    });

  const generateInstantBrief = async () => {
    const label = "生成即时工作简报";
    setBusy(label);
    setNotice(null);
    try {
      const payload = await callWorkLedger<WorkLedgerBriefResult>("/api/v1/work-ledger/briefing", {
        method: "POST",
        body: JSON.stringify({
          days: briefDays,
          consult_codex: consultCodexForBrief,
          codex_wait_seconds: codexBriefWaitSeconds,
        }),
      });
      setBriefPreview(payload);
      cacheSuccessfulInstantBrief(payload);
      setNotice(
        `已生成${payload.days === 1 ? "今天" : `最近 ${payload.days} 天`}的工作简报，聚合 ${payload.session_count} 个任务；${
          payload.generation_mode === "llm_evidence_editor" ? "已完成 AI 证据整理和质量校验" : "当前为证据基础版"
        }。`,
      );
    } catch (error) {
      const fallback = buildDegradedInstantBrief({
        days: briefDays,
        status,
        detail,
        cachedBrief: readCachedInstantBrief(),
        reason: error,
      });
      setBriefPreview(fallback);
      const source =
        fallback.degraded_source === "current_page_snapshot"
          ? `当前页面已加载的 ${fallback.cached_evidence_count ?? 0} 条证据`
          : fallback.degraded_source === "last_successful_brief"
            ? "上次成功生成的简报"
            : "无证据安全说明";
      setNotice(
        `L3 简报接口当前不可用，已降级使用${source}，未采用未经验证的 Codex 内容。原因：${fallback.degraded_reason}`,
      );
    } finally {
      setBusy(null);
    }
  };

  const cancelCodexInvocation = async (invocationId: string) => {
    setCodexCancelBusy(invocationId);
    try {
      await callWorkLedger("/api/v1/work-ledger/codex-cancel", {
        method: "POST",
        body: JSON.stringify({
          invocation_id: invocationId,
          reason: "user_cancelled_from_work_ledger",
        }),
      });
      setNotice("已请求停止 Codex 协作任务；执行器将在当前安全检查点退出。");
    } catch (error) {
      setNotice(`停止 Codex 协作失败：${String(error)}`);
    } finally {
      setCodexCancelBusy(null);
    }
  };

  const copyInstantBrief = async () => {
    const text = briefPreview?.text.trim();
    if (!text) return;
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      setNotice("即时工作简报已复制到剪贴板。");
    } else {
      setNotice("当前环境不支持自动复制，请在预览区手动复制。");
    }
  };

  const endTask = () =>
    run("结束任务", async () => {
      const payload = await callWorkLedger<WorkLedgerDetail>("/api/v1/work-ledger/end", {
        method: "POST",
        body: JSON.stringify({ session_id: active?.session_id, generate_outputs: true }),
      });
      setNotice("已结束任务，并生成日报与续写 Prompt。");
      return payload.session.session_id;
    });

  const refreshProcessInbox = () =>
    run("扫描今日过程", async () => {
      const sid = detail?.session.session_id || active?.session_id;
      if (!sid) throw new Error("no session selected");
      const payload = await callWorkLedger<WorkProcessInboxResponse>("/api/v1/work-ledger/process-inbox/refresh", {
        method: "POST",
        body: JSON.stringify({ session_id: sid }),
      });
      setProcessInbox(payload.inbox);
      setNotice(`已归并 ${payload.inbox.candidate_count} 个来源候选，形成 ${payload.inbox.event_count} 个工作事件。`);
      return sid;
    });

  const reviewProcessInbox = (eventId: string, action: "accepted" | "rejected" | "ignored") =>
    run(action === "accepted" ? "采纳过程事件" : action === "rejected" ? "拒绝过程事件" : "忽略过程事件", async () => {
      const sid = detail?.session.session_id || active?.session_id;
      if (!sid) throw new Error("no session selected");
      const payload = await callWorkLedger<WorkProcessInboxResponse & { event: WorkProcessInboxEvent }>("/api/v1/work-ledger/process-inbox/review", {
        method: "POST",
        body: JSON.stringify({ session_id: sid, event_id: eventId, action, generate_outputs: action === "accepted" }),
      });
      setProcessInbox(payload.inbox);
      setNotice(action === "accepted" ? "已采纳并写入任务时间线，日报与续写资产已刷新。" : action === "rejected" ? "已拒绝，本次内容不会进入工作资产。" : "已忽略，本次不采用且不降低来源质量。" );
      return sid;
    });

  const reviewProjectFact = (candidateId: string, action: "merge" | "separate" | "dismiss") =>
    run(action === "merge" ? "合并项目事实" : action === "separate" ? "保持事实独立" : "忽略事实建议", async () => {
      const sid = detail?.session.session_id || active?.session_id;
      if (!sid) throw new Error("no session selected");
      await callWorkLedger<{ ok: boolean; index: { summary: WorkProjectFacts["summary"] } }>(
        "/api/v1/work-ledger/project-facts/review",
        {
          method: "POST",
          body: JSON.stringify({ session_id: sid, candidate_id: candidateId, action }),
        },
      );
      const refreshed = await callWorkLedger<WorkProjectFactsResponse>(
        `/api/v1/work-ledger/sessions/${encodeURIComponent(sid)}/project-facts`,
      );
      setProjectFacts(refreshed.facts);
      setNotice(
        action === "merge"
          ? "已按你的确认合并为同一项目事实，来源和历史出现记录均已保留。"
          : action === "separate"
            ? "已确认这是两个独立事实，后续不会自动互相覆盖。"
            : "已忽略本次相似建议，两个事实保持独立。",
      );
      return sid;
    });

  const updateProjectFact = (factId: string, targetState: "open" | "in_progress" | "completed" | "reopened") =>
    run("更新项目事实状态", async () => {
      const sid = detail?.session.session_id || active?.session_id;
      if (!sid) throw new Error("no session selected");
      await callWorkLedger<{ ok: boolean }>("/api/v1/work-ledger/project-facts/update", {
        method: "POST",
        body: JSON.stringify({
          session_id: sid,
          fact_id: factId,
          target_state: targetState,
          reason: `User changed project fact state to ${targetState} from Work Ledger console.`,
          failure_reason: targetState === "reopened" ? "User confirmed the fact needs to be reopened." : "",
        }),
      });
      const refreshed = await callWorkLedger<WorkProjectFactsResponse>(
        `/api/v1/work-ledger/sessions/${encodeURIComponent(sid)}/project-facts`,
      );
      setProjectFacts(refreshed.facts);
      setNotice(
        targetState === "completed"
          ? "该事实已确认完成，日报会把这次状态变化计入成果。"
          : targetState === "reopened"
            ? "该事实已重新打开，续写任务书会优先带入。"
            : "项目事实状态已更新。",
      );
      return sid;
    });

  const reviewMethodology = (candidateId: string, action: "approve" | "reject") =>
    run(action === "approve" ? "批准方法论" : "否决方法论", async () => {
      const sid = detail?.session.session_id || active?.session_id;
      if (!sid) throw new Error("no session selected");
      await callWorkLedger<{ ok: boolean }>("/api/v1/work-ledger/methodology/review", {
        method: "POST",
        body: JSON.stringify({
          session_id: sid,
          candidate_id: candidateId,
          action,
        }),
      });
      const refreshed = await callWorkLedger<WorkProjectOutcomesResponse>(
        `/api/v1/work-ledger/sessions/${encodeURIComponent(sid)}/project-outcomes`,
      );
      setProjectOutcomes(refreshed.outcomes);
      setNotice(
        action === "approve"
          ? "方法论已批准，后续周报和续作可以引用，并保留原始事实与证据关系。"
          : "方法论候选已否决，不会进入长期方法论或周报成果。",
      );
      return sid;
    });

  const recordOutcomeValue = (
    outcomeId: string,
    eventType: "delivered" | "adopted" | "impact_confirmed" | "feedback_positive" | "feedback_neutral" | "feedback_negative",
  ) =>
    run("记录成果价值", async () => {
      const sid = detail?.session.session_id || active?.session_id;
      if (!sid) throw new Error("no session selected");
      await callWorkLedger<{ ok: boolean }>(
        "/api/v1/work-ledger/value-events",
        {
          method: "POST",
          body: JSON.stringify({
            session_id: sid,
            event_type: eventType,
            outcome_ids: [outcomeId],
            channel: eventType === "delivered" ? "user_confirmed" : "",
            idempotency_key: `${eventType}:${outcomeId}:${Date.now()}`,
          }),
        },
      );
      const refreshed = await callWorkLedger<WorkValueChainResponse>(
        `/api/v1/work-ledger/sessions/${encodeURIComponent(sid)}/value-chain`,
      );
      setValueChain(refreshed.value_chain);
      setNotice(
        eventType === "delivered"
          ? "已记录真实交付。"
          : eventType === "adopted"
            ? "已记录真实采用。"
            : eventType === "impact_confirmed"
              ? "已记录可确认影响。"
              : "价值反馈已记录；它只影响价值排序，不会改写原始事实。",
      );
      return sid;
    });

  const recordMethodologyReuse = (methodologyId: string, success: boolean) =>
    run(success ? "记录方法论复用成功" : "记录方法论复用失败", async () => {
      const sid = detail?.session.session_id || active?.session_id;
      if (!sid) throw new Error("no session selected");
      await callWorkLedger<{ ok: boolean }>("/api/v1/work-ledger/value-events", {
        method: "POST",
        body: JSON.stringify({
          session_id: sid,
          event_type: success ? "methodology_reused" : "methodology_reuse_failed",
          methodology_id: methodologyId,
          idempotency_key: `methodology-reuse:${methodologyId}:${Date.now()}`,
        }),
      });
      setNotice(
        success
          ? "已记录方法论在真实任务中复用成功。"
          : "已记录本次复用失败，原方法论仍保留并等待后续修正。",
      );
      return sid;
    });

  const configureSourceRoots = () =>
    run("保存来源目录", async () => {
      const sid = detail?.session.session_id || active?.session_id;
      if (!sid) throw new Error("no session selected");
      const roots = sourceRoots.split(/\r?\n|;/).map((item) => item.trim()).filter(Boolean);
      const payload = await callWorkLedger<WorkSourceStatusResponse>("/api/v1/work-ledger/source-configure", {
        method: "POST",
        body: JSON.stringify({ session_id: sid, roots }),
      });
      setSourceStatus(payload.status);
      setSourceRoots(payload.status.configured_roots.join("\n"));
      setNotice("来源目录已保存。系统只会读取这里明确允许的本地历史或导出内容。");
      return sid;
    });

  const controlSource = (action: "pause" | "resume" | "reset" | "reset_all", sourceKey = "") =>
    run(action === "pause" ? "暂停来源" : action === "resume" ? "恢复来源" : "重置来源游标", async () => {
      const sid = detail?.session.session_id || active?.session_id;
      if (!sid) throw new Error("no session selected");
      const payload = await callWorkLedger<WorkSourceStatusResponse>("/api/v1/work-ledger/source-control", {
        method: "POST",
        body: JSON.stringify({ session_id: sid, action, source_key: sourceKey }),
      });
      setSourceStatus(payload.status);
      setNotice(action === "pause" ? "该来源已暂停。" : action === "resume" ? "该来源已恢复。" : "读取游标已重置，下次扫描会重新读取。" );
      return sid;
    });

  const revokeSourceRoot = (root = "") =>
    run(root ? "撤销来源授权" : "撤销全部来源授权", async () => {
      const sid = detail?.session.session_id || active?.session_id;
      if (!sid) throw new Error("no session selected");
      const payload = await callWorkLedger<WorkSourceStatusResponse>("/api/v1/work-ledger/source-revoke", {
        method: "POST",
        body: JSON.stringify({ session_id: sid, root }),
      });
      setSourceStatus(payload.status);
      setSourceRoots(payload.status.configured_roots.join("\n"));
      setNotice(root ? "该项目来源授权已撤销，后续任务不会再继承。" : "该项目的全部来源授权已撤销。");
      return sid;
    });

  return (
    <div className="console-fiber-host console-holo-slab flex h-full min-h-0 flex-col !overflow-y-auto overflow-x-hidden p-4">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-cyan-400/15 pb-4">
        <div>
          <p className="text-xs uppercase tracking-[0.32em] text-cyan-300/70">AI Work Memory</p>
          <h1 className="mt-1 text-2xl font-semibold text-cyan-50">今日工作台</h1>
          <p className="mt-1 text-sm text-slate-400">
            记录任务、采集 Git / 文件证据、生成日报和下一轮 Codex / Cursor 任务书。
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {!active ? (
            <button
              className="inline-flex items-center gap-2 rounded-md border border-emerald-400/40 bg-emerald-400/15 px-3 py-2 text-sm font-medium text-emerald-100 hover:bg-emerald-400/20 disabled:opacity-50"
              onClick={startToday}
              disabled={busy !== null || loading}
            >
              <Play className="h-4 w-4" />
              开始记录
            </button>
          ) : (
            <span className="inline-flex h-9 items-center rounded-md border border-emerald-400/25 bg-emerald-400/10 px-3 text-sm text-emerald-200">
              任务记录中
            </span>
          )}
          <select
            aria-label="简报时间范围"
            className="h-9 rounded-md border border-cyan-400/25 bg-slate-950/70 px-2 text-sm text-cyan-50 outline-none focus:border-cyan-300"
            value={briefDays}
            onChange={(event) => setBriefDays(Number(event.target.value))}
            disabled={busy !== null}
          >
            <option value={1}>今天</option>
            <option value={7}>近 7 天</option>
            <option value={30}>近 30 天</option>
          </select>
          <label className="inline-flex h-9 items-center gap-2 border border-cyan-400/20 bg-slate-950/55 px-3 text-xs text-slate-300">
            <input
              type="checkbox"
              checked={consultCodexForBrief}
              onChange={(event) => setConsultCodexForBrief(event.target.checked)}
              disabled={busy !== null}
              className="h-4 w-4 accent-cyan-400"
            />
            证据不足时询问 Codex“工作计划”
          </label>
          {consultCodexForBrief ? (
            <select
              aria-label="等待 Codex 回答时限"
              className="h-9 rounded-md border border-cyan-400/25 bg-slate-950/70 px-2 text-xs text-cyan-50 outline-none focus:border-cyan-300"
              value={codexBriefWaitSeconds}
              onChange={(event) => setCodexBriefWaitSeconds(Number(event.target.value))}
              disabled={busy !== null}
            >
              <option value={120}>每个项目等待 2 分钟</option>
              <option value={300}>每个项目等待 5 分钟</option>
              <option value={600}>每个项目等待 10 分钟</option>
            </select>
          ) : null}
          <button
            className="inline-flex items-center gap-2 rounded-md border border-cyan-300/35 bg-cyan-400/15 px-3 py-2 text-sm font-medium text-cyan-50 hover:bg-cyan-400/20 disabled:opacity-50"
            onClick={generateInstantBrief}
            disabled={busy !== null}
          >
            <ClipboardList className="h-4 w-4" />
            {busy === "生成即时工作简报" ? "等待协作并生成..." : "生成简报"}
          </button>
          <button
            className="inline-flex items-center gap-2 rounded-md border border-cyan-400/35 bg-cyan-400/10 px-3 py-2 text-sm text-cyan-100 hover:bg-cyan-400/15"
            onClick={() => void load(undefined)}
            disabled={loading}
          >
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
            刷新
          </button>
        </div>
      </div>

      {notice ? (
        <div className="mt-4 rounded-md border border-cyan-400/25 bg-cyan-400/10 px-4 py-3 text-sm text-cyan-100">
          {notice}
        </div>
      ) : null}

      {codexInvocations.some((item) => ["queued", "running", "waiting"].includes(item.status)) ? (
        <section className="mt-4 border-y border-amber-300/20 bg-amber-300/[0.04] py-4">
          <div className="text-xs uppercase tracking-[0.2em] text-amber-200/70">Codex Invocation Manager</div>
          <div className="mt-1 text-base font-semibold text-amber-50">正在使用 Codex 桌面通道</div>
          <div className="mt-3 space-y-2">
            {codexInvocations
              .filter((item) => ["queued", "running", "waiting"].includes(item.status))
              .map((item) => (
                <div key={item.invocation_id} className="flex flex-wrap items-center justify-between gap-3 border border-amber-300/15 bg-slate-950/45 px-3 py-2.5">
                  <div className="min-w-0">
                    <div className="text-sm text-amber-50">
                      {item.metadata?.project_name || "Codex"} · {item.status === "queued" ? "排队中" : item.status === "waiting" ? "等待回复" : "执行中"}
                    </div>
                    <div className="mt-1 truncate text-xs text-slate-400">
                      阶段：{item.stage || "准备"} · {item.detail || item.invocation_id}
                    </div>
                  </div>
                  <button
                    className="inline-flex items-center gap-1.5 rounded-md border border-rose-300/30 bg-rose-400/10 px-2.5 py-1.5 text-xs text-rose-100 hover:bg-rose-400/15 disabled:opacity-50"
                    onClick={() => void cancelCodexInvocation(item.invocation_id)}
                    disabled={codexCancelBusy === item.invocation_id || item.cancel_requested}
                  >
                    <Square className="h-3.5 w-3.5" />
                    {item.cancel_requested ? "停止中" : "停止"}
                  </button>
                </div>
              ))}
          </div>
        </section>
      ) : null}

      {briefPreview ? (
        <section className="mt-4 border-y border-cyan-300/25 bg-cyan-400/[0.06] py-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="text-xs uppercase tracking-[0.18em] text-cyan-200/80">Instant Brief Preview</div>
              <div className="mt-1 text-sm text-slate-300">
                {briefPreview.days === 1 ? "今天" : `最近 ${briefPreview.days} 天`} · {briefPreview.session_count} 个账本任务 · {briefPreview.activity_day_count ?? 0} 个活跃工作日 · {briefPreview.git_commit_count ?? 0} 个 Git 提交 · {briefPreview.generation_mode === "client_degraded_evidence_baseline" ? briefPreview.cached_evidence_count ?? 0 : briefPreview.changed_file_count} 条{briefPreview.generation_mode === "client_degraded_evidence_baseline" ? "已加载" : "本地"}证据
              </div>
              <div className="mt-1 text-xs text-cyan-200/70">
                {briefPreview.generation_mode === "llm_evidence_editor"
                  ? `AI 证据整理${briefPreview.model ? ` · ${briefPreview.model}` : ""} · 已通过质量门禁`
                  : briefPreview.generation_mode === "client_degraded_evidence_baseline"
                    ? "离线降级版 · 只使用页面快照或上次成功结果"
                    : "证据基础版 · 不把文件变化冒充为已完成成果"}
              </div>
              {briefPreview.codex_consultation ? (
                <div className="mt-1 text-xs text-slate-400">
                  {(briefPreview.codex_consultation.effective_count ??
                    briefPreview.codex_consultation.success_count ??
                    0) > 0
                    ? `Codex 协作：获得或复用 ${
                        briefPreview.codex_consultation.effective_count ??
                        briefPreview.codex_consultation.success_count ??
                        0
                      } 条有效回复`
                    : briefPreview.codex_consultation.consulted
                      ? briefPreview.codex_consultation.results?.some(
                          (item) => item.completion_state?.status === "permission_required",
                        )
                        ? "Codex 协作：Codex 正在等待权限批准，未取得完整回答，本次未参与简报融合"
                        : "Codex 协作：已尝试查询，但没有获得通过验证的回复，本次未参与简报融合"
                    : briefPreview.codex_consultation.reason === "no_report_evidence_gap"
                      ? "Codex 协作：本次证据完整，无需询问"
                      : briefPreview.codex_consultation.reason === "l3_briefing_unavailable"
                        ? "Codex 协作：L3 简报接口不可用，本次未发起或采用 Codex 回复"
                      : briefPreview.codex_consultation.reason === "disabled"
                        ? "Codex 协作：未启用"
                        : `Codex 协作：${briefPreview.codex_consultation.reason || "未执行"}`}
                </div>
              ) : null}
              {briefPreview.codex_fusion ? (
                <div className="mt-1 text-xs text-slate-400">
                  最终融合：
                  {briefPreview.codex_fusion.available_for_final_synthesis
                    ? `可用结论 ${briefPreview.codex_fusion.usable_claim_count ?? 0} 条，实际采用 ${
                        (briefPreview.fusion_trace?.used_claim_ids?.length ?? 0) +
                        (briefPreview.fusion_trace?.used_interpretation_ids?.length ?? 0) +
                        (briefPreview.fusion_trace?.used_recommendation_ids?.length ?? 0)
                      } 条`
                    : "没有经过验证的 Codex 结论，最终报告仅使用 Jachin 本地证据"}
                </div>
              ) : null}
              {briefPreview.codex_execution ? (
                <div
                  className={`mt-2 border-l-2 px-3 py-2 text-xs ${
                    briefPreview.codex_execution.status === "fused"
                      ? "border-emerald-400/70 bg-emerald-400/5 text-emerald-200"
                      : briefPreview.codex_execution.degraded
                        ? "border-amber-400/70 bg-amber-400/5 text-amber-100"
                        : "border-cyan-400/50 bg-cyan-400/5 text-cyan-100"
                  }`}
                >
                  {briefPreview.codex_execution.status === "fused"
                    ? `融合完成：等待并核验了 ${briefPreview.codex_execution.verified_reply_count ?? 0} 条 Codex 回复，最终采用 ${briefPreview.codex_execution.used_claim_count ?? 0} 条结论。`
                    : briefPreview.codex_execution.degraded
                      ? `降级完成：${
                          briefPreview.codex_execution.reason === "l3_briefing_unavailable"
                            ? "L3 简报接口不可用，无法发起或等待 Codex"
                            : briefPreview.codex_execution.reason === "codex_permission_not_approved_before_deadline"
                              ? "等待期内 Codex 权限未获批准"
                              : briefPreview.codex_execution.reason === "codex_reply_timeout"
                                ? "Codex 在等待时限内没有完成回答"
                                : briefPreview.codex_execution.reason === "codex_generation_failed"
                                  ? "Codex 生成失败"
                                  : briefPreview.codex_execution.reason === "codex_reply_failed_validation"
                                    ? "Codex 回答未通过完整性或关联校验"
                                    : briefPreview.codex_execution.reason === "verified_codex_reply_not_consumed_by_final_composer"
                                      ? "Codex 已返回，但最终融合编辑未通过质量门禁"
                                      : "没有获得可安全融合的 Codex 结论"
                         }；最终使用 ${
                           briefPreview.codex_execution.fallback_strategy === "last_successful_brief"
                             ? "上次成功生成并缓存的简报"
                             : briefPreview.codex_execution.fallback_strategy === "client_cached_work_ledger_snapshot"
                               ? "当前页面已加载的工作账本快照"
                               : briefPreview.codex_execution.fallback_strategy === "jachin_llm_over_local_evidence"
                                 ? "Jachin 本地证据与模型整理"
                                 : "Jachin 本地证据基础版"
                        }，未混入未经验证的内容。`
                      : briefPreview.codex_execution.status === "not_needed"
                        ? "Codex 协作未触发：本地证据没有检测到需要补问的缺口。"
                        : "Codex 协作未启用，本次仅使用 Jachin 本地证据。"}
                </div>
              ) : null}
            </div>
            <button
              className="inline-flex items-center gap-2 rounded-md border border-cyan-400/25 bg-cyan-400/10 px-3 py-2 text-xs text-cyan-100 hover:bg-cyan-400/15"
              onClick={copyInstantBrief}
            >
              <Copy className="h-3.5 w-3.5" />
              复制简报
            </button>
          </div>
          <div className="mt-3 max-h-[420px] overflow-auto whitespace-pre-wrap border-t border-cyan-400/10 pt-3 text-sm leading-6 text-cyan-50">
            {briefPreview.text}
          </div>
          {briefPreview.codex_consultation?.results?.length ? (
            <div className="mt-3 border-t border-cyan-400/10 pt-3">
              <div className="text-xs uppercase tracking-[0.16em] text-cyan-200/70">Codex Collaboration Evidence</div>
              <div className="mt-2 space-y-2">
                {briefPreview.codex_consultation.results.map((item, index) => (
                  <div key={`${item.project_name || "project"}-${index}`} className="text-xs text-slate-400">
                    <span className={item.ok ? "text-emerald-300" : "text-amber-300"}>
                      {item.ok ? "已核验" : "需检查"}
                    </span>
                    {" · "}
                    {item.project_name || "未命名项目"} / {item.conversation_name || "工作计划"}
                    {item.deduplicated ? " · 已复用同一批证据的历史回答" : ""}
                    {item.deduplicated
                      ? ""
                      : item.ok
                      ? ` · 回答 ${item.answer_length ?? 0} 字 · ${
                          item.used_in_final_brief
                            ? `最终采用 ${item.used_claim_count ?? 0} 条结论`
                            : "最终未采用"
                        }`
                      : item.completion_state?.status === "permission_required"
                        ? " · 等待 Codex 权限批准，未取得回答"
                        : item.completion_state?.status === "timeout"
                          ? " · 等待 Codex 回答超时，未取得回答"
                          : ` · ${item.tool_detail || "回答未通过完整性与关联校验"}`}
                    {item.evidence_panel_path ? (
                      <div className="mt-1 break-all font-mono text-[11px] text-slate-500">
                        {item.evidence_panel_path}
                      </div>
                    ) : null}
                  </div>
                ))}
              </div>
            </div>
          ) : null}
          <div className="mt-3 break-all font-mono text-[11px] text-slate-500">{briefPreview.path}</div>
          {briefPreview.codex_execution_path ? (
            <div className="mt-1 break-all font-mono text-[11px] text-slate-500">
              协作记录：{briefPreview.codex_execution_path}
            </div>
          ) : null}
        </section>
      ) : null}

      {reliability ? (
        <section className="mt-4 border-y border-cyan-400/15 bg-slate-950/35 py-4">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <div className="text-xs uppercase tracking-[0.24em] text-cyan-300/70">7 Day Reliability</div>
              <div className="mt-1 text-lg font-semibold text-cyan-50">工作资产闭环健康度</div>
            </div>
            <div className="text-right">
              <div className="font-mono text-3xl text-emerald-300">{Math.round(reliability.metrics.overall_score)}</div>
              <div className="text-[11px] text-slate-500">满分 100 · 按七个自然日计算</div>
            </div>
          </div>
          <div className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 text-xs md:grid-cols-4 xl:grid-cols-8">
            <div><div className="text-slate-500">连续使用</div><div className="mt-1 text-base text-cyan-50">{reliability.metrics.current_streak} 天</div></div>
            <div><div className="text-slate-500">任务收工率</div><div className="mt-1 text-base text-cyan-50">{Math.round(reliability.metrics.completion_rate * 100)}%</div></div>
            <div><div className="text-slate-500">资产形成率</div><div className="mt-1 text-base text-cyan-50">{Math.round(reliability.metrics.asset_formation_rate * 100)}%</div></div>
            <div><div className="text-slate-500">输出采纳率</div><div className="mt-1 text-base text-cyan-50">{Math.round(reliability.metrics.output_adoption_rate * 100)}%</div></div>
            <div><div className="text-slate-500">次日续接命中</div><div className="mt-1 text-base text-cyan-50">{Math.round(reliability.metrics.continuation_hit_rate * 100)}%</div></div>
            <div><div className="text-slate-500">成果交付率</div><div className="mt-1 text-base text-cyan-50">{Math.round((reliability.metrics.outcome_delivery_rate ?? 0) * 100)}%</div></div>
            <div><div className="text-slate-500">成果采用率</div><div className="mt-1 text-base text-cyan-50">{Math.round((reliability.metrics.outcome_adoption_rate ?? 0) * 100)}%</div></div>
            <div><div className="text-slate-500">续作实际使用</div><div className="mt-1 text-base text-cyan-50">{Math.round((reliability.metrics.continuation_use_rate ?? 0) * 100)}%</div></div>
          </div>
          <div className="mt-4 grid grid-cols-7 gap-2">
            {reliability.daily.map((day) => {
              const tone = day.status === "healthy" ? "bg-emerald-400" : day.status === "partial" ? "bg-cyan-400" : day.status === "attention" ? "bg-amber-400" : "bg-slate-700";
              return (
                <div key={day.date} className="min-w-0 text-center">
                  <div className="flex h-16 items-end bg-white/[0.03]">
                    <div className={`w-full ${tone}`} style={{ height: `${Math.max(4, Math.min(100, day.health_score || 0))}%` }} />
                  </div>
                  <div className="mt-1 truncate text-[10px] text-slate-500">{day.date.slice(5)}</div>
                  <div className="font-mono text-[11px] text-slate-300">{Math.round(day.health_score)}</div>
                </div>
              );
            })}
          </div>
          {reliability.reminders.length ? (
            <div className="mt-4 border-t border-amber-400/15 pt-3">
              <div className="text-xs font-medium text-amber-200">需要补齐的工作资产</div>
              <div className="mt-2 space-y-1 text-xs text-slate-400">
                {reliability.reminders.slice(0, 3).map((item) => (
                  <div key={`${item.kind}-${item.session_id}`}>· {item.title || item.session_id}：{item.message}</div>
                ))}
              </div>
            </div>
          ) : (
            <div className="mt-4 border-t border-emerald-400/15 pt-3 text-xs text-emerald-200">最近七天没有发现未完成的工作资产缺口。</div>
          )}
        </section>
      ) : null}

      <div className="mt-4 grid min-h-0 flex-1 gap-4 xl:grid-cols-[380px_minmax(0,1fr)]">
        <section className="flex min-h-0 flex-col gap-4">
          <div className="rounded-lg border border-cyan-400/15 bg-slate-950/55 p-4">
            <div className="flex items-center justify-between gap-3">
              <div>
                <div className="text-xs uppercase tracking-[0.22em] text-slate-500">Current Task</div>
                <h2 className="mt-1 text-lg font-semibold text-cyan-50">
                  {active ? active.title : "没有活动任务"}
                </h2>
              </div>
              <span className={`rounded-md border px-2 py-1 text-xs ${active ? "border-emerald-400/30 bg-emerald-400/10 text-emerald-200" : "border-slate-500/30 bg-slate-500/10 text-slate-400"}`}>
                {active ? "进行中" : "空闲"}
              </span>
            </div>
            {active ? (
              <div className="mt-3 space-y-2 text-sm text-slate-300">
                <div>项目：{active.project_name || basename(active.project_path)}</div>
                <div className="break-all font-mono text-xs text-cyan-100/80">{active.project_path}</div>
                <div>证据：{active.evidence_count ?? 0} 条</div>
                <div>开始：{active.start_time || "-"}</div>
              </div>
            ) : (
              <div className="mt-4 space-y-3">
                <label className="block text-sm text-slate-300">
                  任务名
                  <input
                    className="mt-1 w-full rounded-md border border-cyan-400/20 bg-slate-950/70 px-3 py-2 text-sm text-cyan-50 outline-none focus:border-cyan-300"
                    value={title}
                    onChange={(e) => setTitle(e.target.value)}
                    placeholder="例如：优化 Jachin 常开语音"
                  />
                </label>
                <label className="block text-sm text-slate-300">
                  项目路径
                  <input
                    className="mt-1 w-full rounded-md border border-cyan-400/20 bg-slate-950/70 px-3 py-2 font-mono text-xs text-cyan-50 outline-none focus:border-cyan-300"
                    value={projectPath}
                    onChange={(e) => setProjectPath(e.target.value)}
                  />
                </label>
                <label className="block text-sm text-slate-300">
                  目标
                  <textarea
                    className="mt-1 min-h-[88px] w-full resize-none rounded-md border border-cyan-400/20 bg-slate-950/70 px-3 py-2 text-sm text-cyan-50 outline-none focus:border-cyan-300"
                    value={goal}
                    onChange={(e) => setGoal(e.target.value)}
                    placeholder="写清楚今天要解决的问题、交付物或验证标准。"
                  />
                </label>
                <button
                  className="inline-flex w-full items-center justify-center gap-2 rounded-md border border-emerald-400/35 bg-emerald-400/15 px-3 py-2 text-sm font-medium text-emerald-100 hover:bg-emerald-400/20 disabled:opacity-50"
                  onClick={startTask}
                  disabled={!title.trim() || busy !== null}
                >
                  <Play className="h-4 w-4" />
                  开始任务
                </button>
              </div>
            )}
          </div>

          <div className="rounded-lg border border-cyan-400/15 bg-slate-950/55 p-4">
            <div className="text-xs uppercase tracking-[0.22em] text-slate-500">Actions</div>
            <div className="mt-3 grid grid-cols-2 gap-2">
              <button className="col-span-2 inline-flex items-center justify-center gap-2 rounded-md border border-cyan-400/35 bg-cyan-400/10 px-3 py-2 text-sm text-cyan-100 hover:bg-cyan-400/15 disabled:opacity-50" disabled={!!active || busy !== null} onClick={startToday}>
                <CalendarPlus className="h-4 w-4" />
                开始今天工作
              </button>
              <button className="inline-flex items-center justify-center rounded-md border border-cyan-400/25 bg-cyan-400/10 px-3 py-2 text-sm text-cyan-100 hover:bg-cyan-400/15 disabled:opacity-50" disabled={!active || busy !== null} onClick={collect}>采集证据</button>
              <button className="inline-flex items-center justify-center rounded-md border border-cyan-400/25 bg-cyan-400/10 px-3 py-2 text-sm text-cyan-100 hover:bg-cyan-400/15 disabled:opacity-50" disabled={!active || busy !== null} onClick={checkpoint}>记录检查点</button>
              <button className="inline-flex items-center justify-center rounded-md border border-cyan-400/25 bg-cyan-400/10 px-3 py-2 text-sm text-cyan-100 hover:bg-cyan-400/15 disabled:opacity-50" disabled={!detail || busy !== null} onClick={generate}>生成输出</button>
              <button className="inline-flex items-center justify-center rounded-md border border-amber-400/30 bg-amber-400/10 px-3 py-2 text-sm text-amber-100 hover:bg-amber-400/15 disabled:opacity-50" disabled={!active || busy !== null} onClick={previewEndDay}>收工预览</button>
              <button className="inline-flex items-center justify-center rounded-md border border-emerald-400/35 bg-emerald-400/10 px-3 py-2 text-sm text-emerald-100 hover:bg-emerald-400/15 disabled:opacity-50" disabled={!active || busy !== null || !!endDayPreview?.safety?.blocked} onClick={finalizeEndDay}>确认收工</button>
              <button className="inline-flex items-center justify-center gap-2 rounded-md border border-cyan-400/25 bg-cyan-400/10 px-3 py-2 text-sm text-cyan-100 hover:bg-cyan-400/15 disabled:opacity-50" disabled={!detail || busy !== null} onClick={copyLarkBrief}>
                <Copy className="h-4 w-4" />
                复制 Lark 短版
              </button>
              <button className="col-span-2 inline-flex items-center justify-center gap-2 rounded-md border border-emerald-400/35 bg-emerald-400/10 px-3 py-2 text-sm text-emerald-100 hover:bg-emerald-400/15 disabled:opacity-50" disabled={!active || busy !== null} onClick={endTask}>
                <CheckCircle2 className="h-4 w-4" />
                结束任务
              </button>
            </div>
            {busy ? <div className="mt-3 text-xs text-cyan-300">{busy}中...</div> : null}
          </div>

          <div className="rounded-lg border border-cyan-400/15 bg-slate-950/55 p-4">
            <div className="text-xs uppercase tracking-[0.22em] text-slate-500">Manual Note</div>
            <textarea
              className="mt-3 min-h-[110px] w-full resize-none rounded-md border border-cyan-400/20 bg-slate-950/70 px-3 py-2 text-sm text-cyan-50 outline-none focus:border-cyan-300"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="记录一下：这次失败原因是..."
              disabled={!active}
            />
            <button
              className="mt-2 inline-flex w-full items-center justify-center gap-2 rounded-md border border-cyan-400/35 bg-cyan-400/10 px-3 py-2 text-sm text-cyan-100 hover:bg-cyan-400/15 disabled:opacity-50"
              onClick={addNote}
              disabled={!active || !note.trim() || busy !== null}
            >
              <NotebookPen className="h-4 w-4" />
              写入过程记录
            </button>
            <button
              className="mt-2 inline-flex w-full items-center justify-center gap-2 rounded-md border border-emerald-400/30 bg-emerald-400/10 px-3 py-2 text-sm text-emerald-100 hover:bg-emerald-400/15 disabled:opacity-50"
              onClick={importAiTraceFromClipboard}
              disabled={!active || busy !== null}
            >
              <Copy className="h-4 w-4" />
              从剪贴板导入 Codex / Cursor 过程
            </button>
          </div>

          <div className="rounded-lg border border-cyan-400/15 bg-slate-950/55 p-4">
            <div className="text-xs uppercase tracking-[0.22em] text-slate-500">AI Process Import</div>
            <textarea
              className="mt-3 min-h-[118px] w-full resize-none rounded-md border border-cyan-400/20 bg-slate-950/70 px-3 py-2 text-sm text-cyan-50 outline-none focus:border-cyan-300"
              value={aiTraceText}
              onChange={(e) => setAiTraceText(e.target.value)}
              placeholder="Paste Codex / Cursor output, terminal summary, failed command logs, decisions, next steps..."
              disabled={!active}
            />
            <button
              className="mt-2 inline-flex w-full items-center justify-center gap-2 rounded-md border border-emerald-400/30 bg-emerald-400/10 px-3 py-2 text-sm text-emerald-100 hover:bg-emerald-400/15 disabled:opacity-50"
              onClick={importAiTraceText}
              disabled={!active || !aiTraceText.trim() || busy !== null}
            >
              <Archive className="h-4 w-4" />
              导入并刷新上下文包
            </button>
            <div className="mt-3 flex gap-2">
              <input
                className="min-w-0 flex-1 rounded-md border border-cyan-400/20 bg-slate-950/70 px-3 py-2 font-mono text-xs text-cyan-50 outline-none focus:border-cyan-300"
                value={aiTracePath}
                onChange={(e) => setAiTracePath(e.target.value)}
                placeholder="D:\\path\\to\\terminal.log"
                disabled={!active}
              />
              <button
                className="shrink-0 rounded-md border border-cyan-400/25 bg-cyan-400/10 px-3 py-2 text-sm text-cyan-100 hover:bg-cyan-400/15 disabled:opacity-50"
                onClick={importAiTraceFile}
                disabled={!active || !aiTracePath.trim() || busy !== null}
              >
                导入日志
              </button>
            </div>
          </div>

          <div className="rounded-lg border border-cyan-400/15 bg-slate-950/55 p-4">
            <div className="text-xs uppercase tracking-[0.22em] text-slate-500">Work Recall</div>
            <div className="mt-3 flex gap-2">
              <select
                className="w-24 rounded-md border border-cyan-400/20 bg-slate-950/70 px-2 py-2 text-sm text-cyan-50 outline-none focus:border-cyan-300"
                value={recallDays}
                onChange={(e) => setRecallDays(Number(e.target.value))}
              >
                <option value={7}>7 天</option>
                <option value={14}>14 天</option>
                <option value={30}>30 天</option>
              </select>
              <input
                className="min-w-0 flex-1 rounded-md border border-cyan-400/20 bg-slate-950/70 px-3 py-2 text-sm text-cyan-50 outline-none focus:border-cyan-300"
                value={recallQuery}
                onChange={(e) => setRecallQuery(e.target.value)}
                placeholder="例如：上次语音优化做到哪了"
              />
            </div>
            <button
              className="mt-2 inline-flex w-full items-center justify-center gap-2 rounded-md border border-cyan-400/35 bg-cyan-400/10 px-3 py-2 text-sm text-cyan-100 hover:bg-cyan-400/15 disabled:opacity-50"
              onClick={recall}
              disabled={!recallQuery.trim() || busy !== null}
            >
              <Search className="h-4 w-4" />
              召回工作记忆
            </button>
            <button
              className="mt-2 inline-flex w-full items-center justify-center gap-2 rounded-md border border-emerald-400/35 bg-emerald-400/10 px-3 py-2 text-sm text-emerald-100 hover:bg-emerald-400/15 disabled:opacity-50"
              onClick={generateMultiDayWeekly}
              disabled={busy !== null}
            >
              <BookOpenCheck className="h-4 w-4" />
              生成多日周报
            </button>
          </div>
        </section>

        <section className="flex min-h-0 flex-col gap-4">
          <div className="grid gap-3 md:grid-cols-3">
            <Metric title="Git 状态" value={latestGit?.summary || "未采集"} />
            <Metric title="文件扫描" value={latestFiles?.summary || "未采集"} />
            <Metric title="手动记录" value={`${manualNotes.length} 条`} />
          </div>

          <div className="min-h-0 flex-1 overflow-hidden rounded-lg border border-cyan-400/15 bg-slate-950/55">
            <div className="flex items-center justify-between border-b border-cyan-400/10 px-4 py-3">
              <div>
                <div className="text-xs uppercase tracking-[0.22em] text-slate-500">Task Timeline</div>
                <div className="mt-1 text-sm text-slate-300">
                  {timeline?.title || detail?.session.title || "选择或创建任务后显示工作过程"}
                </div>
              </div>
              <span className="text-xs text-slate-500">{timeline?.entry_count ?? evidence.length} 条</span>
            </div>
            <div className="max-h-[520px] overflow-auto p-3">
              {timeline?.entries?.length ? timeline.entries.slice().reverse().map((item) => (
                <div key={item.evidence_id} className="relative border-l border-cyan-400/20 pb-4 pl-4 last:pb-0">
                  <span className="absolute -left-1 top-1.5 h-2 w-2 bg-cyan-300" />
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="rounded border border-cyan-400/20 px-1.5 py-0.5 text-[10px] text-cyan-200">{timelineCategoryLabel(item.category)}</span>
                    <span className={`rounded px-1.5 py-0.5 text-[10px] ${item.actor === "user" ? "bg-emerald-400/10 text-emerald-200" : item.actor === "ai_tool" ? "bg-violet-400/10 text-violet-200" : "bg-slate-400/10 text-slate-300"}`}>
                      {timelineActorLabel(item.actor)}
                    </span>
                    <span className="ml-auto text-[10px] text-slate-600">{item.collected_at || "-"}</span>
                  </div>
                  <div className="mt-1 text-sm leading-5 text-cyan-50">{item.summary}</div>
                  {item.category === "checkpoint" ? (
                    <div className="mt-1 text-[11px] text-slate-500">
                      {item.details?.project_kind === "git" ? "Git 项目" : "文件项目"} · Git 改动 {item.details?.changed_file_count ?? 0} · 文件变化 {item.details?.recent_file_count ?? 0}
                    </div>
                  ) : null}
                  {item.category === "continuation" ? (
                    <div className={`mt-1 text-[11px] ${item.details?.hit ? "text-emerald-300" : "text-amber-300"}`}>
                      {item.details?.hit ? "已成功承接上一任务资产" : "发现上一任务，但上下文资产不完整"}
                    </div>
                  ) : null}
                </div>
              )) : evidence.length ? evidence.slice().reverse().map((item) => <EvidenceRow key={item.evidence_id} item={item} />) : (
                <div className="rounded-md border border-white/10 bg-white/[0.03] p-4 text-sm text-slate-500">
                  暂无工作时间线。开始任务后会自动记录 Git、文件和过程检查点。
                </div>
              )}
            </div>
          </div>

          <section className="rounded-lg border border-cyan-400/15 bg-slate-950/55 p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <div className="flex items-center gap-2 text-xs uppercase tracking-[0.22em] text-slate-500">
                  <BookOpenCheck className="h-3.5 w-3.5" />
                  Project Fact Chain
                </div>
                <div className="mt-1 text-sm text-slate-300">
                  同一事项跨 Codex、终端、Git 和多天任务只保留一个事实身份，同时保留每次出现的来源与证据。
                </div>
              </div>
              <div className="flex flex-wrap gap-3 text-xs text-slate-400">
                <span>事实 {projectFacts?.summary?.fact_count ?? 0}</span>
                <span className="text-emerald-200">已完成 {projectFacts?.summary?.completed_count ?? 0}</span>
                <span className="text-cyan-200">进行中 {projectFacts?.summary?.in_progress_count ?? 0}</span>
                <span className="text-amber-200">
                  未闭环 {(projectFacts?.summary?.open_count ?? 0) + (projectFacts?.summary?.reopened_count ?? 0)}
                </span>
                <span className={projectFacts?.summary?.review_pending ? "text-rose-200" : ""}>
                  待确认 {projectFacts?.summary?.review_pending ?? 0}
                </span>
              </div>
            </div>

            <div className="mt-3 grid gap-2">
              {visibleProjectFacts.length ? visibleProjectFacts.slice(0, 12).map((fact) => {
                const isNew = projectFacts?.new_facts?.some((item) => item.fact_id === fact.fact_id);
                const isContinued = projectFacts?.continued_facts?.some((item) => item.fact_id === fact.fact_id);
                return (
                  <div key={fact.fact_id} className="flex flex-wrap items-start gap-2 border-t border-white/10 pt-2 first:border-t-0 first:pt-0">
                    <span className={`rounded px-1.5 py-0.5 text-[10px] ${isNew ? "bg-emerald-400/10 text-emerald-200" : isContinued ? "bg-cyan-400/10 text-cyan-200" : "bg-amber-400/10 text-amber-200"}`}>
                      {isNew ? "本次新增" : isContinued ? "再次出现" : "历史未闭环"}
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="text-sm text-cyan-50">{fact.canonical_summary}</div>
                      <div className="mt-1 text-[10px] text-slate-500">
                        {fact.source_types.join(" + ") || "confirmed"} · {fact.occurrence_count} 次证据 · 最近 {fact.last_seen_at || "-"}
                      </div>
                      {fact.failure_attempts?.length ? (
                        <div className="mt-1 text-[11px] text-rose-200">最近失败：{fact.failure_attempts[fact.failure_attempts.length - 1]?.text}</div>
                      ) : null}
                      {fact.decisions?.length ? (
                        <div className="mt-1 text-[11px] text-violet-200">决策：{fact.decisions[fact.decisions.length - 1]?.text}</div>
                      ) : null}
                      {fact.next_actions?.length ? (
                        <div className="mt-1 text-[11px] text-cyan-200">下一步：{fact.next_actions[fact.next_actions.length - 1]?.text}</div>
                      ) : null}
                    </div>
                    <div className="flex flex-col items-end gap-1">
                      <span className={`text-[10px] ${projectFactStateClass(fact.state)}`}>
                        {projectFactStateLabel(fact.state)}
                      </span>
                      <div className="flex gap-1">
                        {fact.state !== "completed" && fact.state !== "superseded" ? (
                          <button className="rounded border border-emerald-400/20 px-2 py-1 text-[10px] text-emerald-200 hover:bg-emerald-400/10 disabled:opacity-50" onClick={() => updateProjectFact(fact.fact_id, "completed")} disabled={busy !== null}>完成</button>
                        ) : null}
                        {fact.state === "completed" ? (
                          <button className="rounded border border-amber-400/20 px-2 py-1 text-[10px] text-amber-200 hover:bg-amber-400/10 disabled:opacity-50" onClick={() => updateProjectFact(fact.fact_id, "reopened")} disabled={busy !== null}>重开</button>
                        ) : null}
                        {fact.state === "open" || fact.state === "reopened" ? (
                          <button className="rounded border border-cyan-400/20 px-2 py-1 text-[10px] text-cyan-200 hover:bg-cyan-400/10 disabled:opacity-50" onClick={() => updateProjectFact(fact.fact_id, "in_progress")} disabled={busy !== null}>推进中</button>
                        ) : null}
                      </div>
                    </div>
                  </div>
                );
              }) : (
                <div className="text-sm text-slate-500">
                  采纳过程事件后会形成项目事实。未经用户确认的内容不会直接进入事实链。
                </div>
              )}
            </div>

            {projectFacts?.review_pending?.length ? (
              <div className="mt-4 border-t border-rose-400/15 pt-3">
                <div className="text-xs font-medium text-rose-100">相似事实需要你确认</div>
                <div className="mt-2 grid gap-3">
                  {projectFacts.review_pending.map((candidate) => {
                    const incoming = projectFactById.get(candidate.new_fact_id);
                    const existing = projectFactById.get(candidate.possible_match_fact_id);
                    return (
                      <div key={candidate.candidate_id} className="rounded-md border border-rose-400/20 bg-rose-400/5 p-3">
                        <div className="text-xs leading-5 text-slate-300">
                          <div>新事实：{incoming?.canonical_summary || candidate.new_fact_id}</div>
                          <div>可能相同：{existing?.canonical_summary || candidate.possible_match_fact_id}</div>
                          <div className="text-slate-500">相似度 {Math.round(Number(candidate.similarity?.score || 0) * 100)}%</div>
                        </div>
                        <div className="mt-2 flex flex-wrap gap-2">
                          <button className="rounded border border-emerald-400/25 bg-emerald-400/10 px-2.5 py-1.5 text-xs text-emerald-100 hover:bg-emerald-400/15 disabled:opacity-50" onClick={() => reviewProjectFact(candidate.candidate_id, "merge")} disabled={busy !== null}>确认为同一事实</button>
                          <button className="rounded border border-cyan-400/25 bg-cyan-400/10 px-2.5 py-1.5 text-xs text-cyan-100 hover:bg-cyan-400/15 disabled:opacity-50" onClick={() => reviewProjectFact(candidate.candidate_id, "separate")} disabled={busy !== null}>保持独立</button>
                          <button className="rounded border border-white/10 px-2.5 py-1.5 text-xs text-slate-400 hover:bg-white/5 disabled:opacity-50" onClick={() => reviewProjectFact(candidate.candidate_id, "dismiss")} disabled={busy !== null}>忽略建议</button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            ) : null}
          </section>

          <section className="rounded-lg border border-violet-400/15 bg-slate-950/55 p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <div className="flex items-center gap-2 text-xs uppercase tracking-[0.22em] text-slate-500">
                  <GitBranch className="h-3.5 w-3.5" />
                  Outcome Graph
                </div>
                <div className="mt-1 text-sm text-slate-300">
                  只有用户确认并完成验证的事实才计入成果；方法论必须能追溯失败、决策、动作和最终结果。
                </div>
              </div>
              <div className="flex flex-wrap gap-3 text-xs text-slate-400">
                <span className="text-emerald-200">有效成果 {projectOutcomes?.summary?.active_outcome_count ?? 0}</span>
                <span>历史结果 {projectOutcomes?.summary?.historical_outcome_count ?? 0}</span>
                <span className="text-amber-200">方法论待审 {projectOutcomes?.summary?.methodology_pending_count ?? 0}</span>
                <span className="text-violet-200">已批准 {projectOutcomes?.summary?.methodology_approved_count ?? 0}</span>
              </div>
            </div>

            <div className="mt-3 border-t border-white/10 pt-3">
              <div className="text-xs font-medium text-emerald-100">本次可计入成果</div>
              <div className="mt-2 grid gap-2">
                {projectOutcomes?.outcomes_this_session?.length ? projectOutcomes.outcomes_this_session.map((outcome) => (
                  <div key={outcome.outcome_id} className="border-l-2 border-emerald-400/50 pl-3">
                    <div className="text-sm text-cyan-50">{outcome.summary}</div>
                    <div className="mt-1 text-[11px] text-slate-500">
                      {outcome.completion_reason || "用户确认完成"} · {outcome.completed_at || "-"}
                    </div>
                  </div>
                )) : (
                  <div className="text-sm text-slate-500">
                    当前任务还没有符合成果口径的完成事实。文件变化和证据数量不会被包装成成果。
                  </div>
                )}
              </div>
            </div>

            {projectOutcomes?.methodology_pending?.length ? (
              <div className="mt-4 border-t border-amber-400/15 pt-3">
                <div className="text-xs font-medium text-amber-100">方法论候选需要你确认</div>
                <div className="mt-2 grid gap-3">
                  {projectOutcomes.methodology_pending.map((candidate) => (
                    <div key={candidate.candidate_id} className="border-l-2 border-amber-400/40 pl-3">
                      <div className="text-sm text-cyan-50">{candidate.title}</div>
                      <div className="mt-1 grid gap-1 text-[11px] leading-5 text-slate-400">
                        <div><span className="text-rose-200">失败：</span>{candidate.trigger}</div>
                        <div><span className="text-violet-200">决策：</span>{candidate.decision}</div>
                        <div><span className="text-cyan-200">动作：</span>{candidate.action}</div>
                        <div><span className="text-emerald-200">结果：</span>{candidate.result}</div>
                      </div>
                      <div className="mt-2 flex gap-2">
                        <button className="rounded border border-emerald-400/25 bg-emerald-400/10 px-2.5 py-1.5 text-xs text-emerald-100 hover:bg-emerald-400/15 disabled:opacity-50" onClick={() => reviewMethodology(candidate.candidate_id, "approve")} disabled={busy !== null}>批准沉淀</button>
                        <button className="rounded border border-rose-400/20 px-2.5 py-1.5 text-xs text-rose-200 hover:bg-rose-400/10 disabled:opacity-50" onClick={() => reviewMethodology(candidate.candidate_id, "reject")} disabled={busy !== null}>否决候选</button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}

            {projectOutcomes?.methodology_approved?.length ? (
              <div className="mt-4 border-t border-violet-400/15 pt-3">
                <div className="text-xs font-medium text-violet-100">已批准方法论</div>
                <div className="mt-2 grid gap-2">
                  {projectOutcomes.methodology_approved.slice(0, 8).map((candidate) => (
                    <div key={candidate.candidate_id} className="flex flex-wrap items-center justify-between gap-2 text-xs leading-5 text-slate-300">
                      <div><span className="text-violet-200">{candidate.title}</span>：{candidate.decision}；{candidate.action}</div>
                      <div className="flex gap-1.5">
                        <button className="rounded border border-emerald-400/20 px-2 py-1 text-[11px] text-emerald-200 hover:bg-emerald-400/10 disabled:opacity-50" onClick={() => recordMethodologyReuse(candidate.candidate_id, true)} disabled={busy !== null}>复用成功</button>
                        <button className="rounded border border-rose-400/20 px-2 py-1 text-[11px] text-rose-200 hover:bg-rose-400/10 disabled:opacity-50" onClick={() => recordMethodologyReuse(candidate.candidate_id, false)} disabled={busy !== null}>复用失败</button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}
          </section>

          <section className="rounded-lg border border-emerald-400/15 bg-slate-950/55 p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <div className="flex items-center gap-2 text-xs uppercase tracking-[0.22em] text-slate-500">
                  <CheckCircle2 className="h-3.5 w-3.5" />
                  Outcome Value Chain
                </div>
                <div className="mt-1 text-sm text-slate-300">
                  完成、交付、采用和产生影响分别记账。价值反馈只改变排序，不会改写已经验证的项目事实。
                </div>
              </div>
              <div className="flex flex-wrap gap-3 text-xs text-slate-400">
                <span>完成 {valueChain?.summary?.active_outcome_count ?? 0}</span>
                <span className="text-cyan-200">交付 {valueChain?.summary?.delivered_outcome_count ?? 0}</span>
                <span className="text-violet-200">采用 {valueChain?.summary?.adopted_outcome_count ?? 0}</span>
                <span className="text-emerald-200">影响 {valueChain?.summary?.impact_outcome_count ?? 0}</span>
                <span>续作使用 {Math.round((valueChain?.summary?.continuation_use_rate ?? 0) * 100)}%</span>
              </div>
            </div>
            <div className="mt-3 grid gap-3 border-t border-white/10 pt-3">
              {valueChain?.outcome_values_this_session?.length ? valueChain.outcome_values_this_session.map((outcome) => (
                <div key={outcome.outcome_id} className="border-l-2 border-emerald-400/40 pl-3">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div>
                      <div className="text-sm text-cyan-50">{outcome.summary}</div>
                      <div className="mt-1 text-[11px] text-slate-500">
                        {valueStageLabel(outcome.value_stage)} · 价值分 {outcome.value_score}
                        {outcome.latest_feedback ? ` · 反馈 ${outcome.latest_feedback}` : ""}
                      </div>
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      <button className="rounded border border-cyan-400/25 px-2 py-1 text-[11px] text-cyan-100 hover:bg-cyan-400/10 disabled:opacity-50" onClick={() => recordOutcomeValue(outcome.outcome_id, "delivered")} disabled={busy !== null}>已交付</button>
                      <button className="rounded border border-violet-400/25 px-2 py-1 text-[11px] text-violet-100 hover:bg-violet-400/10 disabled:opacity-50" onClick={() => recordOutcomeValue(outcome.outcome_id, "adopted")} disabled={busy !== null}>已采用</button>
                      <button className="rounded border border-emerald-400/25 px-2 py-1 text-[11px] text-emerald-100 hover:bg-emerald-400/10 disabled:opacity-50" onClick={() => recordOutcomeValue(outcome.outcome_id, "impact_confirmed")} disabled={busy !== null}>有影响</button>
                      <button className="rounded border border-white/10 px-2 py-1 text-[11px] text-slate-300 hover:bg-white/5 disabled:opacity-50" onClick={() => recordOutcomeValue(outcome.outcome_id, "feedback_neutral")} disabled={busy !== null}>一般</button>
                      <button className="rounded border border-rose-400/20 px-2 py-1 text-[11px] text-rose-200 hover:bg-rose-400/10 disabled:opacity-50" onClick={() => recordOutcomeValue(outcome.outcome_id, "feedback_negative")} disabled={busy !== null}>没价值</button>
                    </div>
                  </div>
                </div>
              )) : (
                <div className="text-sm text-slate-500">
                  当前任务还没有可追踪价值的已验证成果。
                </div>
              )}
            </div>
          </section>

          <section className="rounded-lg border border-cyan-400/15 bg-slate-950/55 p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <div className="flex items-center gap-2 text-xs uppercase tracking-[0.22em] text-slate-500">
                  <Inbox className="h-3.5 w-3.5" />
                  Daily Process Inbox
                </div>
                <div className="mt-1 text-sm text-slate-300">
                  Codex、Cursor、终端、文档和文件检查点会先脱敏归并，再由你决定是否进入工作资产。
                </div>
              </div>
              <button
                className="inline-flex items-center gap-2 rounded-md border border-cyan-400/30 bg-cyan-400/10 px-3 py-2 text-xs text-cyan-100 hover:bg-cyan-400/15 disabled:opacity-50"
                onClick={refreshProcessInbox}
                disabled={!detail || busy !== null}
              >
                <RefreshCw className="h-3.5 w-3.5" />
                扫描今日过程
              </button>
            </div>
            <div className="mt-3 flex flex-wrap gap-3 text-xs text-slate-400">
              <span>来源候选 {processInbox?.candidate_count ?? 0}</span>
              <span>归并事件 {processInbox?.event_count ?? 0}</span>
              <span className="text-amber-200">待处理 {processInbox?.summary?.pending ?? 0}</span>
              <span className="text-emerald-200">已采纳 {processInbox?.summary?.accepted ?? 0}</span>
            </div>
            {Number(processInbox?.last_refresh?.high_quality_new_event_count || 0) > 0 ? (
              <div className="mt-3 rounded-md border border-cyan-300/25 bg-cyan-300/10 px-3 py-2 text-xs text-cyan-100">
                发现 {Number(processInbox?.last_refresh?.high_quality_new_event_count || 0)} 条高质量工作过程，等待你确认后再进入工作资产。
              </div>
            ) : null}
            <div className="mt-3 border-y border-white/10 py-3">
              <div className="grid gap-2 md:grid-cols-[minmax(0,1fr)_auto]">
                <textarea
                  className="min-h-[66px] w-full resize-y rounded-md border border-cyan-400/20 bg-slate-950/70 px-3 py-2 font-mono text-xs text-cyan-50 outline-none focus:border-cyan-300"
                  value={sourceRoots}
                  onChange={(event) => setSourceRoots(event.target.value)}
                  placeholder={"每行一个明确允许的 Codex / Cursor / 终端历史或导出目录\n留空时使用当前任务项目目录"}
                  disabled={!detail}
                />
                <div className="flex gap-2 md:flex-col">
                  <button className="rounded-md border border-cyan-400/25 bg-cyan-400/10 px-3 py-2 text-xs text-cyan-100 hover:bg-cyan-400/15 disabled:opacity-50" onClick={configureSourceRoots} disabled={!detail || busy !== null}>保存目录</button>
                  <button className="rounded-md border border-amber-400/25 bg-amber-400/10 px-3 py-2 text-xs text-amber-100 hover:bg-amber-400/15 disabled:opacity-50" onClick={() => controlSource("reset_all")} disabled={!sourceStatus?.source_count || busy !== null}>全部重扫</button>
                  <button className="rounded-md border border-rose-400/20 bg-rose-400/5 px-3 py-2 text-xs text-rose-200 hover:bg-rose-400/10 disabled:opacity-50" onClick={() => revokeSourceRoot()} disabled={!sourceStatus?.authorizations?.length || busy !== null}>撤销全部</button>
                </div>
              </div>
              <div className="mt-2 flex flex-wrap gap-3 text-[11px] text-slate-500">
                <span>已登记 {sourceStatus?.source_count ?? 0} 个来源</span>
                {sourceStatus?.source_profile?.inherited ? (
                  <span className="text-cyan-200">已继承上次项目授权</span>
                ) : null}
                <span>暂停 {sourceStatus?.paused_count ?? 0}</span>
                <span className={sourceStatus?.error_count ? "text-rose-300" : ""}>错误 {sourceStatus?.error_count ?? 0}</span>
                <span className={sourceStatus?.backoff_count ? "text-amber-300" : ""}>退避 {sourceStatus?.backoff_count ?? 0}</span>
                <span>本次新增行 {sourceStatus?.last_refresh?.new_line_count ?? 0}</span>
                <span>未变化跳过 {sourceStatus?.last_refresh?.sources_skipped_unchanged ?? 0}</span>
                <span>平均同步 {sourceStatus?.health?.average_duration_ms ?? 0} ms</span>
                <span>累计同步 {sourceStatus?.health?.sync_count ?? 0} 次</span>
                <span>变更命中 {sourceStatus?.health?.changed_sync_count ?? 0} 次</span>
              </div>
              {sourceStatus?.authorizations?.length ? (
                <div className="mt-3 border-t border-white/10">
                  {sourceStatus.authorizations.map((authorization) => (
                    <div key={authorization.path} className="flex flex-wrap items-center gap-2 border-b border-white/10 py-2 text-xs last:border-b-0">
                      <span className={`rounded px-1.5 py-0.5 text-[10px] ${authorization.exists && authorization.readable ? "bg-emerald-400/10 text-emerald-200" : "bg-rose-400/10 text-rose-200"}`}>
                        {authorization.exists && authorization.readable ? "可读取" : "不可用"}
                      </span>
                      <span className="min-w-0 flex-1 truncate font-mono text-[10px] text-slate-400" title={authorization.path}>{authorization.path}</span>
                      <span className="text-[10px] text-slate-500">{authorization.source_count} 个来源</span>
                      <span className="text-[10px] text-slate-500">{authorization.total_line_count} 行</span>
                      <button
                        className="rounded border border-rose-400/20 px-2 py-1 text-[10px] text-rose-200 hover:bg-rose-400/10"
                        onClick={() => revokeSourceRoot(authorization.path)}
                        disabled={busy !== null}
                      >
                        撤销
                      </button>
                    </div>
                  ))}
                </div>
              ) : null}
              {sourceStatus?.sources?.length ? (
                <div className="mt-3 max-h-[180px] overflow-auto border-t border-white/10">
                  {sourceStatus.sources.map((source) => (
                    <div key={source.source_key} className="flex flex-wrap items-center gap-2 border-b border-white/10 py-2 text-xs last:border-b-0">
                      <span className={`rounded px-1.5 py-0.5 text-[10px] ${source.paused || source.status === "backoff" ? "bg-amber-400/10 text-amber-200" : source.status === "error" ? "bg-rose-400/10 text-rose-200" : "bg-emerald-400/10 text-emerald-200"}`}>{source.paused ? "暂停" : source.status || "ready"}</span>
                      <span className="text-cyan-100">{source.source_type || "source"}</span>
                      <span className="min-w-0 flex-1 truncate font-mono text-[10px] text-slate-500" title={source.source_uri}>{source.source_uri}</span>
                      <span className="text-[10px] text-slate-600">累计 {source.total_line_count ?? 0} 行</span>
                      <button className="rounded border border-white/10 px-2 py-1 text-[10px] text-slate-300 hover:bg-white/5" onClick={() => controlSource(source.paused ? "resume" : "pause", source.source_key)} disabled={busy !== null}>{source.paused ? "恢复" : "暂停"}</button>
                      <button className="rounded border border-white/10 px-2 py-1 text-[10px] text-slate-300 hover:bg-white/5" onClick={() => controlSource("reset", source.source_key)} disabled={busy !== null}>重扫</button>
                      {source.last_error ? <div className="basis-full text-[10px] text-rose-300">{source.last_error}</div> : null}
                    </div>
                  ))}
                </div>
              ) : null}
            </div>
            <div className="mt-3 grid gap-2">
              {(processInbox?.events || []).length ? (processInbox?.events || []).map((event) => (
                <div key={event.event_id} className="border-t border-white/10 pt-3 first:border-t-0 first:pt-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={`rounded px-1.5 py-0.5 text-[10px] ${event.status === "accepted" ? "bg-emerald-400/10 text-emerald-200" : event.status === "rejected" ? "bg-rose-400/10 text-rose-200" : event.status === "ignored" ? "bg-slate-400/10 text-slate-300" : event.status === "blocked" ? "bg-amber-400/10 text-amber-200" : "bg-cyan-400/10 text-cyan-200"}`}>
                      {event.status === "accepted" ? "已采纳" : event.status === "rejected" ? "已拒绝" : event.status === "ignored" ? "已忽略" : event.status === "blocked" ? "已阻断" : "待处理"}
                    </span>
                    <span className="text-[11px] text-slate-500">{event.source_types.join(" + ")} · {event.source_count} 个依据</span>
                    <span className="ml-auto font-mono text-[11px] text-cyan-200">质量 {Math.round(event.quality_score)}</span>
                  </div>
                  <div className="mt-1 text-sm font-medium text-cyan-50">{event.summary}</div>
                  {event.excerpt ? <div className="mt-1 line-clamp-2 text-xs leading-5 text-slate-400">{event.excerpt}</div> : null}
                  <div className="mt-2 flex flex-wrap gap-2">
                    {event.source_chain.slice(0, 4).map((source) => (
                      <span key={`${source.source_type}-${source.source_uri}`} className="max-w-[280px] truncate rounded border border-white/10 px-1.5 py-0.5 font-mono text-[10px] text-slate-500" title={source.source_uri}>
                        {source.source_type}: {source.source_uri}
                      </span>
                    ))}
                  </div>
                  {event.status === "pending" ? (
                    <div className="mt-3 flex gap-2">
                      <button className="rounded-md border border-emerald-400/25 bg-emerald-400/10 px-2.5 py-1.5 text-xs text-emerald-100 hover:bg-emerald-400/15 disabled:opacity-50" onClick={() => reviewProcessInbox(event.event_id, "accepted")} disabled={busy !== null}>采纳</button>
                      <button className="rounded-md border border-rose-400/25 bg-rose-400/10 px-2.5 py-1.5 text-xs text-rose-100 hover:bg-rose-400/15 disabled:opacity-50" onClick={() => reviewProcessInbox(event.event_id, "rejected")} disabled={busy !== null}>拒绝</button>
                      <button className="rounded-md border border-slate-400/20 bg-slate-400/10 px-2.5 py-1.5 text-xs text-slate-300 hover:bg-slate-400/15 disabled:opacity-50" onClick={() => reviewProcessInbox(event.event_id, "ignored")} disabled={busy !== null}>忽略</button>
                    </div>
                  ) : null}
                </div>
              )) : (
                <div className="border-t border-white/10 pt-3 text-sm text-slate-500">尚未扫描。只会读取任务项目目录、Work Ledger 导入目录和你明确允许的来源。</div>
              )}
            </div>
          </section>

          <div className="rounded-lg border border-cyan-400/15 bg-slate-950/55 p-4">
            <div className="text-xs uppercase tracking-[0.22em] text-slate-500">Outputs</div>
            <div className="mt-3 grid gap-2 md:grid-cols-2">
              <OutputPath label="任务上下文包" path={detail?.session.output_paths?.context_pack} />
              <OutputPath label="工作复盘七问" path={detail?.session.output_paths?.work_review} />
              <OutputPath label="逐条工作汇报" path={detail?.session.output_paths?.work_report_summary} />
              <OutputPath label="日报" path={detail?.session.output_paths?.daily_report} />
              <OutputPath label="Codex / Cursor 续写 Prompt" path={detail?.session.output_paths?.codex_continuation_prompt} />
              <OutputPath label="团队 Lark 简报" path={detail?.session.output_paths?.team_lark_brief} />
              <OutputPath label="周报草稿" path={detail?.session.output_paths?.weekly_report} />
              <OutputPath label="绩效材料条目" path={detail?.session.output_paths?.performance_entries} />
              <OutputPath label="方法论候选" path={detail?.session.output_paths?.methodology_candidates} />
              <OutputPath label="增强日报" path={detail?.session.output_paths?.enhanced_daily_report} />
              <OutputPath label="增强续写 Prompt" path={detail?.session.output_paths?.enhanced_continuation_prompt} />
              <OutputPath label="Lark 短版" path={detail?.session.output_paths?.lark_brief} />
              <OutputPath label="质量门控报告" path={detail?.session.output_paths?.llm_quality_report} />
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              <button
                className="inline-flex items-center gap-2 rounded-md border border-cyan-400/30 bg-cyan-400/10 px-3 py-2 text-xs text-cyan-100 hover:bg-cyan-400/15 disabled:opacity-50"
                disabled={!detail || busy !== null}
                onClick={copyWorkReport}
              >
                <Copy className="h-3.5 w-3.5" />
                生成并复制工作汇报
              </button>
              <button
                className="inline-flex items-center gap-2 rounded-md border border-emerald-400/30 bg-emerald-400/10 px-3 py-2 text-xs text-emerald-100 hover:bg-emerald-400/15 disabled:opacity-50"
                disabled={!detail?.session.output_paths?.team_lark_brief || busy !== null}
                onClick={() => adoptOutput("team_lark_brief")}
              >
                <Archive className="h-3.5 w-3.5" />
                采纳团队简报
              </button>
              <button
                className="inline-flex items-center gap-2 rounded-md border border-emerald-400/30 bg-emerald-400/10 px-3 py-2 text-xs text-emerald-100 hover:bg-emerald-400/15 disabled:opacity-50"
                disabled={!detail?.session.output_paths?.weekly_report || busy !== null}
                onClick={() => adoptOutput("weekly_report")}
              >
                <Archive className="h-3.5 w-3.5" />
                采纳周报
              </button>
              <button
                className="inline-flex items-center gap-2 rounded-md border border-emerald-400/30 bg-emerald-400/10 px-3 py-2 text-xs text-emerald-100 hover:bg-emerald-400/15 disabled:opacity-50"
                disabled={!detail?.session.output_paths?.methodology_candidates || busy !== null}
                onClick={() => adoptOutput("methodology_candidates")}
              >
                <Archive className="h-3.5 w-3.5" />
                采纳方法论候选
              </button>
            </div>
          </div>

          {workReportPreview ? (
            <div className="rounded-lg border border-cyan-400/20 bg-cyan-400/5 p-4">
              <div className="mb-2 flex items-center gap-2 text-xs uppercase tracking-[0.18em] text-cyan-200/80">
                <ClipboardList className="h-3.5 w-3.5" />
                Itemized Work Report
              </div>
              <div className="whitespace-pre-wrap text-sm leading-6 text-cyan-50">{workReportPreview}</div>
            </div>
          ) : null}

          {larkBriefPreview ? (
            <div className="rounded-lg border border-emerald-400/20 bg-emerald-400/10 p-4">
              <div className="mb-2 flex items-center gap-2 text-xs uppercase tracking-[0.18em] text-emerald-200/80">
                <Send className="h-3.5 w-3.5" />
                Lark Brief Preview
              </div>
              <div className="whitespace-pre-wrap text-sm leading-6 text-emerald-50">{larkBriefPreview}</div>
            </div>
          ) : null}

          {candidateQuality?.ranked_sources?.length ? (
            <section className="border-y border-cyan-400/15 bg-slate-950/35 py-4">
              <div className="flex flex-wrap items-end justify-between gap-3">
                <div>
                  <div className="text-xs uppercase tracking-[0.22em] text-cyan-200/70">候选来源质量</div>
                  <div className="mt-1 text-sm text-slate-300">
                    最近 {candidateQuality.window_days} 天 · {candidateQuality.summary.source_count} 类来源 · {candidateQuality.totals.total} 次反馈
                  </div>
                </div>
                <div className="flex gap-3 text-xs text-slate-400">
                  <span className="text-emerald-300">采纳 {candidateQuality.totals.accepted}</span>
                  <span className="text-rose-300">拒绝 {candidateQuality.totals.rejected}</span>
                  <span className="text-amber-300">阻断 {candidateQuality.totals.blocked}</span>
                </div>
              </div>
              <div className="mt-4 divide-y divide-white/10 border-y border-white/10">
                {candidateQuality.ranked_sources.map((row) => {
                  const adjustment = Number(row.score_adjustment || 0);
                  const adjustmentClass = adjustment > 0 ? "text-emerald-300" : adjustment < 0 ? "text-rose-300" : "text-slate-400";
                  return (
                    <div key={row.quality_key} className="grid gap-2 py-3 text-xs md:grid-cols-[minmax(150px,1fr)_2fr_auto] md:items-center">
                      <div>
                        <div className="font-medium text-cyan-50">{candidateSourceLabel(row.quality_key)}</div>
                        <div className="mt-0.5 font-mono text-[10px] text-slate-600">{row.quality_key}</div>
                      </div>
                      <div>
                        <div className="flex justify-between text-[11px] text-slate-400">
                          <span>接受率 {Math.round((row.accept_rate || 0) * 100)}%</span>
                          <span>采纳 {row.accepted} · 拒绝 {row.rejected} · 阻断 {row.blocked}</span>
                        </div>
                        <div className="mt-1 h-1.5 overflow-hidden bg-white/10">
                          <div className="h-full bg-emerald-400" style={{ width: `${Math.max(0, Math.min(100, (row.accept_rate || 0) * 100))}%` }} />
                        </div>
                      </div>
                      <div className={`font-mono text-sm ${adjustmentClass}`}>
                        {adjustment > 0 ? "+" : ""}{adjustment.toFixed(2)} 分
                      </div>
                    </div>
                  );
                })}
              </div>
              <div className="mt-2 text-[11px] text-slate-500">
                分数来自真实采纳、拒绝和敏感阻断记录，会直接影响下一次候选排序；收工预览会把本次依据写入 Evidence。
              </div>
            </section>
          ) : null}

          {endDayPreview ? (
            <div className={`rounded-lg border p-4 ${endDayPreview.safety?.blocked ? "border-amber-400/30 bg-amber-400/10" : "border-emerald-400/20 bg-emerald-400/10"}`}>
              <div className="mb-2 flex items-center gap-2 text-xs uppercase tracking-[0.18em] text-emerald-200/80">
                <CheckCircle2 className="h-3.5 w-3.5" />
                End Day Preview
              </div>
              <div className="text-sm text-cyan-50">
                候选证据组：{endDayPreview.candidates?.length ?? 0}
                {endDayPreview.safety?.blocked ? " · 检测到敏感内容，需处理后再确认" : " · 安全检查通过"}
              </div>
              {endDayPreview.safety?.types?.length ? (
                <div className="mt-1 text-xs text-amber-200">风险类型：{endDayPreview.safety.types.join(", ")}</div>
              ) : null}
              <div className="mt-3 grid gap-2">
                {(endDayPreview.candidates || []).map((item, index) => (
                  <div key={`${item.kind}-${index}`} className="rounded-md border border-white/10 bg-white/[0.03] p-3">
                    <div className="flex items-center gap-2">
                      <span className="rounded border border-cyan-400/20 px-1.5 py-0.5 text-[11px] uppercase text-cyan-200">{item.kind}</span>
                      <span className="text-xs text-slate-400">{item.count ?? 0} items</span>
                      {typeof item.score === "number" ? <span className="ml-auto font-mono text-[11px] text-cyan-200">候选分 {item.score.toFixed(2)}</span> : null}
                    </div>
                    <div className="mt-1 text-sm text-cyan-50">{item.summary || "待采集候选"}</div>
                    {item.source?.quality_key ? (
                      <div className="mt-1 text-[11px] text-slate-500">
                        来源：{candidateSourceLabel(item.source.quality_key)}
                        {item.source.quality?.total ? ` · 历史反馈 ${item.source.quality.total} 次` : " · 暂无历史反馈"}
                        {item.source.quality?.score_adjustment ? ` · 调整 ${item.source.quality.score_adjustment > 0 ? "+" : ""}${item.source.quality.score_adjustment}` : ""}
                      </div>
                    ) : null}
                    {item.reason ? <div className="mt-1 text-[11px] text-cyan-200/60">排序依据：{item.reason}</div> : null}
                    {item.sample?.length ? (
                      <div className="mt-2 line-clamp-3 text-xs leading-5 text-slate-400">{item.sample.join(" / ")}</div>
                    ) : null}
                    {item.source?.file_path ? (
                      <div className="mt-2 flex flex-wrap items-center gap-2">
                        <div className="min-w-0 flex-1 truncate font-mono text-[11px] text-slate-500">{item.source.file_path}</div>
                        <button
                          className="shrink-0 rounded-md border border-cyan-400/25 bg-cyan-400/10 px-2 py-1 text-xs text-cyan-100 hover:bg-cyan-400/15"
                          onClick={() => setAiTracePath(item.source?.file_path || "")}
                        >
                          使用这个文件
                        </button>
                        <button
                          className="shrink-0 rounded-md border border-emerald-400/25 bg-emerald-400/10 px-2 py-1 text-xs text-emerald-100 hover:bg-emerald-400/15 disabled:opacity-50"
                          onClick={() => adoptCandidate(item.source?.file_path || "")}
                          disabled={busy !== null}
                        >
                          采纳并导入
                        </button>
                        <button
                          className="shrink-0 rounded-md border border-rose-400/25 bg-rose-400/10 px-2 py-1 text-xs text-rose-100 hover:bg-rose-400/15 disabled:opacity-50"
                          onClick={() => rejectCandidate(item.source?.file_path || "")}
                          disabled={busy !== null}
                        >
                          拒绝
                        </button>
                      </div>
                    ) : null}
                  </div>
                ))}
              </div>
            </div>
          ) : null}

          {recallResult ? (
            <div className="rounded-lg border border-cyan-400/15 bg-slate-950/55 p-4">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="text-xs uppercase tracking-[0.22em] text-slate-500">Recall Result</div>
                  <div className="mt-1 text-sm text-cyan-100">
                    最近 {recallResult.window_days} 天 · {recallResult.hit_count} 条命中
                  </div>
                </div>
                <span className="rounded-md border border-cyan-400/20 px-2 py-1 text-xs text-slate-400">
                  sessions {recallResult.index_summary.session_count}
                </span>
              </div>
              <div className="mt-3 grid gap-2">
                {recallResult.hits.length ? recallResult.hits.map((hit, index) => (
                  <div key={`${hit.kind}-${hit.session_id}-${index}`} className="rounded-md border border-white/10 bg-white/[0.03] p-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="rounded border border-cyan-400/20 px-1.5 py-0.5 text-[11px] uppercase text-cyan-200">{hit.kind}</span>
                      {hit.trust_level ? <span className="rounded border border-emerald-400/20 px-1.5 py-0.5 text-[11px] text-emerald-200">{hit.trust_level}</span> : null}
                      <span className="ml-auto text-[11px] text-slate-500">score {Math.round((hit.score ?? 0) * 10) / 10}</span>
                    </div>
                    <div className="mt-1 text-sm font-medium text-cyan-50">{hit.title || hit.project_name || hit.session_id}</div>
                    <div className="mt-1 line-clamp-3 text-xs leading-5 text-slate-400">{hit.text || hit.path || "无摘要"}</div>
                    {hit.ranking_reason ? <div className="mt-2 text-[11px] text-cyan-200/70">依据：{hit.ranking_reason}</div> : null}
                  </div>
                )) : (
                  <div className="rounded-md border border-white/10 bg-white/[0.03] p-3 text-sm text-slate-500">
                    没有命中。可以换一个项目名、文件名或任务关键词。
                  </div>
                )}
              </div>
            </div>
          ) : null}

          {weeklyPreview ? (
            <div className="rounded-lg border border-emerald-400/20 bg-emerald-400/10 p-4">
              <div className="mb-2 flex items-center gap-2 text-xs uppercase tracking-[0.18em] text-emerald-200/80">
                <BookOpenCheck className="h-3.5 w-3.5" />
                Weekly Report Preview
              </div>
              <div className="text-xs text-slate-400">文件：{weeklyPreview.path}</div>
              <div className="mt-3 max-h-[260px] overflow-auto whitespace-pre-wrap text-sm leading-6 text-emerald-50">
                {weeklyPreview.text.slice(0, 2800)}
              </div>
            </div>
          ) : null}

          <div className="rounded-lg border border-cyan-400/15 bg-slate-950/55 p-4">
            <div className="flex items-center justify-between gap-3">
              <div>
                <div className="text-xs uppercase tracking-[0.22em] text-slate-500">Project Memory</div>
                <div className="mt-1 text-sm text-slate-300">
                  {projectMemory?.project_count ? `已记住 ${projectMemory.project_count} 个项目别名` : "尚未形成项目记忆"}
                </div>
              </div>
              {projectMemory?.recent ? (
                <span className="rounded-md border border-emerald-400/20 bg-emerald-400/10 px-2 py-1 text-xs text-emerald-200">
                  recent
                </span>
              ) : null}
            </div>
            {projectMemory?.recent ? (
              <div className="mt-3 rounded-md border border-white/10 bg-white/[0.03] p-3">
                <div className="text-xs uppercase tracking-[0.18em] text-cyan-200/80">最近续接</div>
                <div className="mt-1 truncate text-sm font-medium text-cyan-50">
                  {projectMemory.recent.project_name || projectMemory.recent.last_title || "未命名项目"}
                </div>
                <div className="mt-1 break-all font-mono text-xs text-slate-400">{projectMemory.recent.project_path || "-"}</div>
                {projectMemory.recent.last_user_goal ? (
                  <div className="mt-2 line-clamp-2 text-xs leading-5 text-slate-400">{projectMemory.recent.last_user_goal}</div>
                ) : null}
              </div>
            ) : null}
            <div className="mt-3 grid gap-2">
              {rememberedProjects.slice(0, 5).map((item, index) => (
                <div key={`${item.alias || item.project_path || index}`} className="rounded-md border border-white/10 bg-white/[0.03] p-3">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-sm font-medium text-cyan-50">
                      {item.alias || item.project_name || basename(item.project_path)}
                    </span>
                    <span className="ml-auto rounded border border-cyan-400/20 px-1.5 py-0.5 text-[11px] text-cyan-200">
                      {Math.round((item.confidence ?? 0) * 100)}%
                    </span>
                  </div>
                  <div className="mt-1 truncate text-xs text-slate-400">{item.last_title || item.project_name || "最近任务未记录"}</div>
                  <div className="mt-1 break-all font-mono text-[11px] text-slate-500">{item.project_path || "-"}</div>
                </div>
              ))}
              {rememberedProjects.length === 0 ? (
                <div className="rounded-md border border-white/10 bg-white/[0.03] p-3 text-sm text-slate-500">
                  开始或结束一次项目任务后，这里会显示可复用的项目路径和最近任务。
                </div>
              ) : null}
            </div>
          </div>

          <div className="rounded-lg border border-cyan-400/15 bg-slate-950/55 p-4">
            <div className="text-xs uppercase tracking-[0.22em] text-slate-500">Recent Sessions</div>
            <div className="mt-3 grid gap-2">
              {(status?.recent_sessions ?? []).slice(0, 6).map((item) => (
                <button
                  key={item.session_id}
                  className="flex items-center justify-between gap-3 rounded-md border border-white/10 bg-white/[0.03] px-3 py-2 text-left text-sm hover:border-cyan-400/30"
                  onClick={() => void load(item.session_id)}
                >
                  <span className="min-w-0">
                    <span className="block truncate text-cyan-50">{item.title}</span>
                    <span className="block truncate text-xs text-slate-500">{item.project_path}</span>
                  </span>
                  <span className="shrink-0 text-xs text-slate-400">{item.status}</span>
                </button>
              ))}
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}

export function WorkLedgerPanel() {
  const [activeTab, setActiveTab] = useState<"ledger" | "value-lab">("ledger");

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden">
      <div className="shrink-0 border-b border-cyan-400/15 bg-slate-950/75 px-4 pt-3">
        <div className="flex items-center gap-1" role="tablist" aria-label="工作账本视图">
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === "ledger"}
            onClick={() => setActiveTab("ledger")}
            className={`inline-flex h-10 items-center gap-2 border-b-2 px-3 text-sm transition-colors ${
              activeTab === "ledger"
                ? "border-cyan-300 text-cyan-50"
                : "border-transparent text-slate-400 hover:text-cyan-100"
            }`}
          >
            <ClipboardList className="h-4 w-4" />
            工作账本
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === "value-lab"}
            onClick={() => setActiveTab("value-lab")}
            className={`inline-flex h-10 items-center gap-2 border-b-2 px-3 text-sm transition-colors ${
              activeTab === "value-lab"
                ? "border-amber-300 text-amber-100"
                : "border-transparent text-slate-400 hover:text-amber-100"
            }`}
          >
            <Bug className="h-4 w-4" />
            价值链测试
            <span className="rounded border border-amber-400/25 px-1.5 py-0.5 text-[10px] uppercase text-amber-200/80">
              Dev
            </span>
          </button>
        </div>
      </div>

      <div className="min-h-0 flex-1">
        {activeTab === "ledger" ? (
          <WorkLedgerWorkspace />
        ) : (
          <WorkLedgerValueLab embedded onOpenLedger={() => setActiveTab("ledger")} />
        )}
      </div>
    </div>
  );
}

function Metric({ title, value }: { title: string; value: string }) {
  return (
    <div className="rounded-lg border border-cyan-400/15 bg-slate-950/55 p-4">
      <div className="text-xs uppercase tracking-[0.22em] text-slate-500">{title}</div>
      <div className="mt-2 line-clamp-2 text-sm font-medium text-cyan-50">{value}</div>
    </div>
  );
}

function EvidenceRow({ item }: { item: WorkEvidence }) {
  const Icon = sourceIcon(item.source);
  return (
    <div className="mb-2 rounded-md border border-white/10 bg-white/[0.03] p-3">
      <div className="flex items-start gap-3">
        <div className="rounded-md border border-cyan-400/20 bg-cyan-400/10 p-2 text-cyan-200">
          <Icon className="h-4 w-4" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium text-cyan-50">{sourceLabel(item.source)}</span>
            <span className="rounded border border-white/10 px-1.5 py-0.5 text-[11px] text-slate-400">{item.trust_level}</span>
            <span className="ml-auto text-xs text-slate-500">{item.collected_at}</span>
          </div>
          <div className="mt-1 break-words text-sm text-slate-300">{item.summary}</div>
        </div>
      </div>
    </div>
  );
}

function OutputPath({ label, path }: { label: string; path?: string }) {
  return (
    <div className="rounded-md border border-white/10 bg-white/[0.03] p-3">
      <div className="text-xs text-slate-500">{label}</div>
      <div className="mt-1 break-all font-mono text-xs text-cyan-100/90">{path || "暂未生成"}</div>
    </div>
  );
}
