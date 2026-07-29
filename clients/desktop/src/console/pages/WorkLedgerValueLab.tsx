import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  ClipboardCopy,
  FlaskConical,
  RefreshCw,
  Send,
  XCircle,
} from "lucide-react";
import { getL3SkillsBaseUrl } from "../../lib/api";

type Session = {
  session_id: string;
  title: string;
  project_name?: string;
  project_path?: string;
  status?: string;
};

type OutcomeValue = {
  outcome_id: string;
  summary: string;
  status: string;
  value_stage: string;
  value_score: number;
  latest_feedback?: string;
  delivered_count?: number;
  adoption_count?: number;
  impact_count?: number;
};

type ValueEvent = {
  value_event_id: string;
  event_type: string;
  session_id: string;
  related_session_id?: string;
  outcome_ids?: string[];
  output_key?: string;
  channel?: string;
  note?: string;
  evidence_id?: string;
  recorded_at: string;
};

type ValueSummary = {
  active_outcome_count?: number;
  delivered_outcome_count?: number;
  adopted_outcome_count?: number;
  impact_outcome_count?: number;
  continuation_available_count?: number;
  continuation_used_count?: number;
  continuation_use_rate?: number;
  methodology_reuse_attempt_count?: number;
  methodology_reuse_success_count?: number;
  methodology_reuse_success_rate?: number;
};

type ValueContext = {
  chain: {
    project_path?: string;
    events: ValueEvent[];
    outcome_values: OutcomeValue[];
    summary: ValueSummary;
  };
  events_this_session: ValueEvent[];
  outcome_values_this_session: OutcomeValue[];
  summary: ValueSummary;
};

type DiagnosticCheck = {
  name: string;
  ok: boolean;
  severity: "pass" | "warning" | "error" | string;
  detail: string;
  evidence?: unknown;
};

type Diagnostic = {
  log_id: string;
  status: "passed" | "warning" | "failed" | string;
  ok: boolean;
  duration_ms: number;
  checks: DiagnosticCheck[];
  counts: {
    checks: number;
    passed: number;
    warnings: number;
    errors: number;
    events: number;
    outcomes: number;
  };
  log_paths: {
    jsonl: string;
    markdown: string;
  };
};

type DiagnosticLog = {
  log_id: string;
  recorded_at: string;
  event: string;
  status: string;
  session_id?: string;
  summary?: string;
  details?: unknown;
};

type LogPayload = {
  entries: DiagnosticLog[];
  count: number;
  paths: {
    jsonl: string;
    markdown: string;
  };
};

async function callLab<T>(path: string, init?: RequestInit): Promise<T> {
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

function stageLabel(stage: string) {
  if (stage === "impact") return "有影响";
  if (stage === "adopted") return "已采用";
  if (stage === "delivered") return "已交付";
  return "已完成";
}

function eventLabel(event: string) {
  const labels: Record<string, string> = {
    delivered: "真实交付",
    adopted: "真实采用",
    impact_confirmed: "确认影响",
    feedback_positive: "正向反馈",
    feedback_neutral: "中性反馈",
    feedback_negative: "负向反馈",
    continuation_available: "续作可用",
    continuation_used: "续作采用",
    methodology_reused: "方法论复用成功",
    methodology_reuse_failed: "方法论复用失败",
  };
  return labels[event] || event;
}

export function WorkLedgerValueLab({
  embedded = false,
  onOpenLedger,
}: {
  embedded?: boolean;
  onOpenLedger?: () => void;
}) {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [sessionId, setSessionId] = useState("");
  const [valueContext, setValueContext] = useState<ValueContext | null>(null);
  const [diagnostic, setDiagnostic] = useState<Diagnostic | null>(null);
  const [logs, setLogs] = useState<LogPayload | null>(null);
  const [selectedOutcomeId, setSelectedOutcomeId] = useState("");
  const [eventType, setEventType] = useState("delivered");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");

  const loadLogs = useCallback(async () => {
    const payload = await callLab<{ ok: boolean; logs: LogPayload }>(
      "/api/v1/work-ledger/value-chain/diagnostics/logs?limit=80",
    );
    setLogs(payload.logs);
  }, []);

  const loadSession = useCallback(async (sid: string) => {
    if (!sid) {
      setValueContext(null);
      return;
    }
    const payload = await callLab<{ ok: boolean; value_chain: ValueContext }>(
      `/api/v1/work-ledger/sessions/${encodeURIComponent(sid)}/value-chain`,
    );
    setValueContext(payload.value_chain);
    const outcomes = payload.value_chain.chain?.outcome_values || [];
    setSelectedOutcomeId((current) =>
      outcomes.some((row) => row.outcome_id === current)
        ? current
        : outcomes[0]?.outcome_id || "",
    );
  }, []);

  const loadAll = useCallback(async () => {
    setBusy("refresh");
    setNotice("");
    try {
      const status = await callLab<{
        ok: boolean;
        active_session?: Session | null;
        recent_sessions: Session[];
      }>("/api/v1/work-ledger/status");
      const nextSessions = status.recent_sessions || [];
      setSessions(nextSessions);
      const nextId =
        sessionId ||
        status.active_session?.session_id ||
        nextSessions[0]?.session_id ||
        "";
      if (nextId !== sessionId) setSessionId(nextId);
      await Promise.all([loadSession(nextId), loadLogs()]);
    } catch (error) {
      setNotice(`加载失败：${String(error)}`);
    } finally {
      setBusy("");
    }
  }, [loadLogs, loadSession, sessionId]);

  useEffect(() => {
    void loadAll();
  }, []);

  useEffect(() => {
    if (!sessionId) return;
    void loadSession(sessionId).catch((error) =>
      setNotice(`加载 Value Chain 失败：${String(error)}`),
    );
  }, [loadSession, sessionId]);

  const selectedSession = useMemo(
    () => sessions.find((row) => row.session_id === sessionId),
    [sessionId, sessions],
  );
  const summary = valueContext?.chain?.summary || {};
  const outcomes = valueContext?.chain?.outcome_values || [];
  const events = valueContext?.chain?.events || [];

  const runDiagnostic = async () => {
    if (!sessionId) return;
    setBusy("diagnostic");
    setNotice("");
    try {
      const payload = await callLab<{ ok: boolean; diagnostic: Diagnostic }>(
        "/api/v1/work-ledger/value-chain/diagnostics/run",
        {
          method: "POST",
          body: JSON.stringify({ session_id: sessionId }),
        },
      );
      setDiagnostic(payload.diagnostic);
      await loadLogs();
      setNotice(
        payload.diagnostic.status === "passed"
          ? "诊断通过，结果已写入 Markdown 和 JSONL 日志。"
          : "诊断发现需要处理的问题，已写入日志。",
      );
    } catch (error) {
      await loadLogs().catch(() => undefined);
      setNotice(`诊断失败：${String(error)}`);
    } finally {
      setBusy("");
    }
  };

  const recordTestEvent = async () => {
    if (!sessionId) return;
    const requiresOutcome = eventType.startsWith("feedback_") || eventType === "impact_confirmed";
    if (requiresOutcome && !selectedOutcomeId) {
      setNotice("这个事件必须先选择一个已验证成果。");
      return;
    }
    setBusy("event");
    setNotice("");
    try {
      await callLab<{ ok: boolean }>("/api/v1/work-ledger/value-events", {
        method: "POST",
        body: JSON.stringify({
          session_id: sessionId,
          event_type: eventType,
          outcome_ids: selectedOutcomeId ? [selectedOutcomeId] : [],
          note,
          channel: eventType === "delivered" ? "value_lab" : "",
          impact_value: eventType === "impact_confirmed" ? note : "",
          idempotency_key: `value-lab:${sessionId}:${eventType}:${Date.now()}`,
        }),
      });
      await loadSession(sessionId);
      setNote("");
      setNotice("测试事件已写入真实 Value Chain，并同步生成 Evidence。");
    } catch (error) {
      await loadLogs().catch(() => undefined);
      setNotice(`事件写入失败：${String(error)}`);
    } finally {
      setBusy("");
    }
  };

  const copyPath = async (value?: string) => {
    if (!value) return;
    await navigator.clipboard.writeText(value);
    setNotice("日志路径已复制。");
  };

  return (
    <div
      className={
        embedded
          ? "h-full overflow-auto p-4"
          : "console-fiber-host console-holo-slab h-full overflow-auto p-5"
      }
    >
      <header className="flex flex-wrap items-start justify-between gap-4 border-b border-cyan-400/15 pb-4">
        <div>
          <div className="text-xs uppercase tracking-[0.28em] text-cyan-300/70">
            Work Ledger Diagnostic
          </div>
          <h1 className="mt-1 text-2xl font-semibold text-cyan-50">
            成果价值链测试
          </h1>
          <p className="mt-1 max-w-3xl text-sm text-slate-400">
            检查完成、交付、采用、影响和续作链是否一致。诊断不修改业务状态；手动测试事件会明确写入 Evidence 和排障日志。
          </p>
        </div>
        <button
          className="inline-flex items-center gap-2 rounded border border-cyan-400/30 bg-cyan-400/10 px-3 py-2 text-sm text-cyan-100 hover:bg-cyan-400/15 disabled:opacity-50"
          onClick={() => void loadAll()}
          disabled={Boolean(busy)}
        >
          <RefreshCw className={`h-4 w-4 ${busy === "refresh" ? "animate-spin" : ""}`} />
          刷新全部
        </button>
      </header>

      {notice ? (
        <div className="mt-4 border-l-2 border-cyan-400 bg-cyan-400/5 px-4 py-3 text-sm text-cyan-100">
          {notice}
        </div>
      ) : null}

      <section className="mt-4 border-y border-cyan-400/15 bg-slate-950/35 py-3">
        <div className="flex flex-wrap items-center gap-x-5 gap-y-2 px-1 text-xs">
          <span className="font-medium text-cyan-100">测试流程</span>
          <span className={sessionId ? "text-emerald-300" : "text-amber-200"}>
            1. {sessionId ? "已选择任务" : "先开始任务"}
          </span>
          <span className={events.length ? "text-emerald-300" : "text-slate-400"}>
            2. 写入测试事件
          </span>
          <span className={diagnostic ? "text-emerald-300" : "text-slate-400"}>
            3. 运行一致性诊断
          </span>
          <span className={logs?.entries?.length ? "text-emerald-300" : "text-slate-400"}>
            4. 查看排障日志
          </span>
          {!sessionId && onOpenLedger ? (
            <button
              type="button"
              onClick={onOpenLedger}
              className="ml-auto rounded border border-emerald-400/35 bg-emerald-400/10 px-3 py-1.5 text-emerald-100 hover:bg-emerald-400/15"
            >
              去开始记录
            </button>
          ) : null}
        </div>
      </section>

      <section className="mt-4 border-y border-white/10 py-4">
        <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_auto_auto]">
          <label className="text-xs text-slate-400">
            测试 Session
            <select
              value={sessionId}
              onChange={(event) => {
                setSessionId(event.target.value);
                setDiagnostic(null);
              }}
              className="mt-1 w-full rounded border border-white/10 bg-slate-950 px-3 py-2 text-sm text-cyan-50"
            >
              {sessions.map((session) => (
                <option key={session.session_id} value={session.session_id}>
                  {session.title} · {session.project_name || "未绑定"} · {session.status}
                </option>
              ))}
            </select>
          </label>
          <button
            className="mt-5 inline-flex items-center justify-center gap-2 rounded border border-emerald-400/30 bg-emerald-400/10 px-4 py-2 text-sm text-emerald-100 hover:bg-emerald-400/15 disabled:opacity-50"
            onClick={() => void runDiagnostic()}
            disabled={!sessionId || Boolean(busy)}
          >
            <FlaskConical className="h-4 w-4" />
            运行一致性诊断
          </button>
          <div className="mt-5 text-right text-xs text-slate-500">
            <div>{selectedSession?.project_path || "未绑定项目路径"}</div>
            <div className="mt-1 font-mono">{sessionId || "-"}</div>
          </div>
        </div>
      </section>

      <section className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
        {[
          ["成果", summary.active_outcome_count || 0],
          ["已交付", summary.delivered_outcome_count || 0],
          ["已采用", summary.adopted_outcome_count || 0],
          ["有影响", summary.impact_outcome_count || 0],
          ["续作机会", summary.continuation_available_count || 0],
          ["续作使用", summary.continuation_used_count || 0],
          ["方法尝试", summary.methodology_reuse_attempt_count || 0],
          ["方法成功", summary.methodology_reuse_success_count || 0],
        ].map(([label, value]) => (
          <div key={String(label)} className="border-l border-cyan-400/25 px-3 py-2">
            <div className="text-[11px] text-slate-500">{label}</div>
            <div className="mt-1 font-mono text-xl text-cyan-50">{value}</div>
          </div>
        ))}
      </section>

      <div className="mt-5 grid gap-5 xl:grid-cols-[minmax(0,1.25fr)_minmax(360px,0.75fr)]">
        <div className="space-y-5">
          <section className="rounded border border-violet-400/15 bg-slate-950/50 p-4">
            <div className="flex items-center gap-2 text-sm font-medium text-violet-100">
              <Activity className="h-4 w-4" />
              Value Chain 数据
            </div>
            <div className="mt-3 space-y-3">
              {outcomes.length ? outcomes.map((outcome) => (
                <div key={outcome.outcome_id} className="border-l-2 border-violet-400/40 pl-3">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div>
                      <div className="text-sm text-cyan-50">{outcome.summary}</div>
                      <div className="mt-1 text-[11px] text-slate-500">
                        {stageLabel(outcome.value_stage)} · 分数 {outcome.value_score}
                        {outcome.latest_feedback ? ` · ${outcome.latest_feedback}` : ""}
                      </div>
                    </div>
                    <div className="font-mono text-[10px] text-slate-600">{outcome.outcome_id}</div>
                  </div>
                </div>
              )) : (
                <div className="text-sm text-slate-500">该项目还没有已验证成果。</div>
              )}
            </div>
          </section>

          <section className="rounded border border-cyan-400/15 bg-slate-950/50 p-4">
            <div className="text-sm font-medium text-cyan-100">写入测试事件</div>
            <div className="mt-3 grid gap-3 md:grid-cols-2">
              <label className="text-xs text-slate-400">
                事件类型
                <select
                  value={eventType}
                  onChange={(event) => setEventType(event.target.value)}
                  className="mt-1 w-full rounded border border-white/10 bg-slate-950 px-3 py-2 text-sm text-cyan-50"
                >
                  {["delivered", "adopted", "impact_confirmed", "feedback_positive", "feedback_neutral", "feedback_negative"].map((type) => (
                    <option key={type} value={type}>{eventLabel(type)}</option>
                  ))}
                </select>
              </label>
              <label className="text-xs text-slate-400">
                关联成果
                <select
                  value={selectedOutcomeId}
                  onChange={(event) => setSelectedOutcomeId(event.target.value)}
                  className="mt-1 w-full rounded border border-white/10 bg-slate-950 px-3 py-2 text-sm text-cyan-50"
                >
                  <option value="">不关联成果</option>
                  {outcomes.map((outcome) => (
                    <option key={outcome.outcome_id} value={outcome.outcome_id}>
                      {outcome.summary}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <label className="mt-3 block text-xs text-slate-400">
              测试说明或影响依据
              <textarea
                value={note}
                onChange={(event) => setNote(event.target.value)}
                rows={3}
                className="mt-1 w-full resize-y rounded border border-white/10 bg-slate-950 px-3 py-2 text-sm text-cyan-50"
                placeholder="例如：该简报已发送给团队并用于周会。"
              />
            </label>
            <button
              className="mt-3 inline-flex items-center gap-2 rounded border border-cyan-400/30 bg-cyan-400/10 px-3 py-2 text-sm text-cyan-100 hover:bg-cyan-400/15 disabled:opacity-50"
              onClick={() => void recordTestEvent()}
              disabled={!sessionId || Boolean(busy)}
            >
              <Send className="h-4 w-4" />
              写入测试事件
            </button>
          </section>

          <section className="rounded border border-white/10 bg-slate-950/50 p-4">
            <div className="text-sm font-medium text-slate-200">最近价值事件</div>
            <div className="mt-3 max-h-80 space-y-2 overflow-auto">
              {events.length ? [...events].reverse().slice(0, 40).map((event) => (
                <div key={event.value_event_id} className="grid gap-1 border-b border-white/5 pb-2 text-xs md:grid-cols-[150px_1fr_auto]">
                  <span className="text-cyan-200">{eventLabel(event.event_type)}</span>
                  <span className="truncate text-slate-400">{event.note || event.output_key || event.value_event_id}</span>
                  <span className="text-slate-600">{event.recorded_at}</span>
                </div>
              )) : (
                <div className="text-sm text-slate-500">暂无价值事件。</div>
              )}
            </div>
          </section>
        </div>

        <div className="space-y-5">
          <section className="rounded border border-emerald-400/15 bg-slate-950/50 p-4">
            <div className="flex items-center justify-between gap-3">
              <div className="text-sm font-medium text-emerald-100">诊断结果</div>
              {diagnostic ? (
                <span className={`text-xs ${diagnostic.status === "passed" ? "text-emerald-300" : diagnostic.status === "warning" ? "text-amber-300" : "text-rose-300"}`}>
                  {diagnostic.status} · {diagnostic.duration_ms} ms
                </span>
              ) : null}
            </div>
            <div className="mt-3 space-y-2">
              {diagnostic?.checks?.length ? diagnostic.checks.map((check) => (
                <div key={check.name} className="flex gap-2 border-b border-white/5 pb-2">
                  {check.ok ? (
                    <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-300" />
                  ) : check.severity === "warning" ? (
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-300" />
                  ) : (
                    <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-rose-300" />
                  )}
                  <div>
                    <div className="font-mono text-xs text-cyan-50">{check.name}</div>
                    <div className="mt-1 text-[11px] leading-5 text-slate-500">{check.detail}</div>
                  </div>
                </div>
              )) : (
                <div className="text-sm text-slate-500">选择 Session 后运行一致性诊断。</div>
              )}
            </div>
          </section>

          <section className="rounded border border-amber-400/15 bg-slate-950/50 p-4">
            <div className="text-sm font-medium text-amber-100">排障日志</div>
            <div className="mt-3 space-y-2 text-xs">
              <button
                className="flex w-full items-center justify-between gap-3 border-b border-white/5 pb-2 text-left"
                onClick={() => void copyPath(logs?.paths.markdown)}
              >
                <span className="min-w-0 truncate text-slate-400">{logs?.paths.markdown || "Markdown 日志尚未创建"}</span>
                <ClipboardCopy className="h-3.5 w-3.5 shrink-0 text-cyan-300" />
              </button>
              <button
                className="flex w-full items-center justify-between gap-3 border-b border-white/5 pb-2 text-left"
                onClick={() => void copyPath(logs?.paths.jsonl)}
              >
                <span className="min-w-0 truncate text-slate-400">{logs?.paths.jsonl || "JSONL 日志尚未创建"}</span>
                <ClipboardCopy className="h-3.5 w-3.5 shrink-0 text-cyan-300" />
              </button>
            </div>
            <div className="mt-3 max-h-96 space-y-2 overflow-auto">
              {logs?.entries?.length ? logs.entries.map((entry) => (
                <div key={entry.log_id} className="border-l border-amber-400/25 pl-3 text-xs">
                  <div className="flex items-center justify-between gap-2">
                    <span className={entry.status === "error" || entry.status === "failed" ? "text-rose-300" : entry.status === "warning" ? "text-amber-300" : "text-emerald-300"}>
                      {entry.event} · {entry.status}
                    </span>
                    <span className="text-slate-600">{entry.recorded_at}</span>
                  </div>
                  <div className="mt-1 text-slate-400">{entry.summary || "-"}</div>
                </div>
              )) : (
                <div className="text-sm text-slate-500">运行一次诊断后会生成日志文档。</div>
              )}
            </div>
          </section>

          <details className="rounded border border-white/10 bg-slate-950/50 p-4">
            <summary className="cursor-pointer text-sm text-slate-300">原始 Value Chain JSON</summary>
            <pre className="mt-3 max-h-96 overflow-auto whitespace-pre-wrap break-all text-[10px] leading-5 text-slate-500">
              {JSON.stringify(valueContext, null, 2)}
            </pre>
          </details>
        </div>
      </div>
    </div>
  );
}
