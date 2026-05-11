/**
 * 游戏测试 — GameQA（Skill + Agent 点火器）
 * 仅调用 ``POST /api/v1/gameqa/run-skill``；执行面由 L3 Agent 读 ``l3_node/skills/gameqa/*.md`` 并调 MCP。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Gamepad2 } from "lucide-react";
import { cn } from "../../utils/cn";
import {
  K11_SMOKE_DEFAULT_TARGET_URL,
  getGameQALogStreamUrlAsync,
  getGameQAUrlRaw,
  postGameQAJson,
} from "../../lib/api";

const SKILL_AUTO = "gameqa_auto_test.md";
const SKILL_SHADOW = "gameqa_shadow_apprentice.md";

export function GameQAPanel() {
  const [targetUrl, setTargetUrl] = useState(K11_SMOKE_DEFAULT_TARGET_URL.trim());
  const [knowledgePath, setKnowledgePath] = useState("");
  const [logs, setLogs] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [sseOk, setSseOk] = useState<boolean | null>(null);
  const logsEndRef = useRef<HTMLDivElement>(null);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    logsEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs]);

  const appendLogs = useCallback((lines: string[]) => {
    setLogs((prev) => [...prev, ...lines.map((s) => (s.startsWith(">") ? s : `> ${s}`))]);
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const streamUrl = await getGameQALogStreamUrlAsync();
        if (cancelled) return;
        const es = new EventSource(streamUrl);
        esRef.current = es;
        es.onopen = () => setSseOk(true);
        es.onmessage = (ev: MessageEvent<string>) => {
          try {
            const data = JSON.parse(ev.data) as { line?: string };
            if (typeof data.line === "string") {
              appendLogs([data.line]);
            }
          } catch {
            /* ignore */
          }
        };
        es.onerror = () => {
          setSseOk(false);
        };
      } catch {
        setSseOk(false);
        appendLogs(["> [WARN] GameQA SSE 无法连接：请确认 L3 已加载 /api/v1/gameqa/log-stream"]);
      }
    })();
    return () => {
      cancelled = true;
      esRef.current?.close();
      esRef.current = null;
    };
  }, [appendLogs]);

  const pushJsonSnippet = useCallback((label: string, obj: unknown) => {
    try {
      const s = JSON.stringify(obj, null, 2);
      setLogs((prev) => [...prev, `> ── ${label} ──`, ...s.split("\n").map((ln) => `> ${ln}`)]);
    } catch {
      appendLogs([`> ${label}: [无法序列化]`]);
    }
  }, [appendLogs]);

  const runSkill = useCallback(
    async (skillName: string) => {
      setBusy(true);
      try {
        const res = await postGameQAJson(
          "run-skill",
          {
            skill_name: skillName,
            url: targetUrl.trim(),
            rules_path: knowledgePath.trim(),
          },
          { bypassCache: true }
        );
        const data = await res.json().catch(() => ({ error: "invalid json" }));
        if (!res.ok) pushJsonSnippet(`HTTP ${res.status} run-skill`, data);
        else pushJsonSnippet("run-skill", data);
      } catch (e) {
        appendLogs([`> [ERROR] run-skill: ${e instanceof Error ? e.message : String(e)}`]);
      } finally {
        setBusy(false);
      }
    },
    [appendLogs, knowledgePath, pushJsonSnippet, targetUrl]
  );

  const onTrainingTail = useCallback(async () => {
    setBusy(true);
    try {
      const url = await getGameQAUrlRaw("training-tail?lines=40", { bypassCache: true });
      const res = await fetch(url);
      const data = await res.json();
      pushJsonSnippet("training-tail", data);
    } catch (e) {
      appendLogs([`> ${e instanceof Error ? e.message : String(e)}`]);
    } finally {
      setBusy(false);
    }
  }, [appendLogs, pushJsonSnippet]);

  const onStop = useCallback(async () => {
    setBusy(true);
    try {
      const res = await postGameQAJson("stop", {}, { bypassCache: true });
      const data = await res.json().catch(() => ({}));
      pushJsonSnippet("stop", data);
    } catch (e) {
      appendLogs([`> [ERROR] stop: ${e instanceof Error ? e.message : String(e)}`]);
    } finally {
      setBusy(false);
    }
  }, [appendLogs, pushJsonSnippet]);

  return (
    <div className="relative flex h-full min-h-0 flex-col gap-5 p-6 text-cyan-300">
      <header className="flex flex-shrink-0 flex-wrap items-center gap-3 border-b border-cyan-500/15 pb-4">
        <div className="flex h-10 w-10 items-center justify-center rounded-lg border border-cyan-500/25 bg-cyan-500/10">
          <Gamepad2 className="h-5 w-5 text-cyan-300" aria-hidden />
        </div>
        <div className="min-w-0 flex-1">
          <h2
            className="font-sci-fi text-lg font-semibold tracking-wide text-white"
            style={{ textShadow: "0 0 12px rgba(34, 211, 238, 0.45)" }}
          >
            ■ 游戏测试 · GameQA
          </h2>
          <p className="text-xs text-cyan-700/90">
            Skill + Agent：<span className="font-mono text-cyan-600/90">POST /api/v1/gameqa/run-skill</span>
            · SSE：<span className="font-mono text-cyan-600/90">log-stream</span>
            {sseOk === true && <span className="ml-2 text-emerald-400/90">● SSE 已连接</span>}
            {sseOk === false && <span className="ml-2 text-amber-400/85">● SSE 未连接</span>}
          </p>
        </div>
      </header>

      <section className="flex flex-shrink-0 flex-col gap-4 rounded-xl border border-cyan-500/20 bg-cyan-500/[0.05] p-4">
        <div className="grid gap-4 md:grid-cols-2">
          <label className="flex flex-col gap-1 text-xs text-cyan-600/90">
            目标站点 URL
            <input
              type="url"
              value={targetUrl}
              disabled={busy}
              onChange={(e) => setTargetUrl(e.target.value)}
              className="w-full rounded border border-cyan-500/35 bg-black/60 px-2 py-1.5 font-mono text-cyan-100 outline-none focus:border-cyan-400/60"
            />
          </label>
          <label className="flex flex-col gap-1 text-xs text-cyan-600/90">
            规则 MD 路径（可空；Agent 将按 Skill 处理）
            <input
              type="text"
              value={knowledgePath}
              disabled={busy}
              onChange={(e) => setKnowledgePath(e.target.value)}
              placeholder="留空则由 Skill 内默认 / tongits_rules.md"
              className="w-full rounded border border-cyan-500/35 bg-black/60 px-2 py-1.5 font-mono text-cyan-100 outline-none focus:border-cyan-400/60"
            />
          </label>
        </div>

        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            disabled={busy}
            onClick={() => void runSkill(SKILL_AUTO)}
            title="L3 Agent + gameqa_auto_test.md + gameqa MCP"
            className={cn(
              "rounded-lg px-4 py-2.5 text-sm font-bold transition-all",
              busy
                ? "cursor-not-allowed bg-slate-800 text-slate-500"
                : "bg-cyan-400 text-black shadow-[0_0_24px_rgba(34,211,238,0.35)] hover:bg-cyan-300"
            )}
          >
            🚀 启动自治测试
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => void runSkill(SKILL_SHADOW)}
            title="L3 Agent + gameqa_shadow_apprentice.md + gameqa MCP"
            className={cn(
              "rounded-lg border px-4 py-2.5 text-sm font-bold transition-all",
              busy
                ? "cursor-not-allowed border-slate-700 bg-slate-900/50 text-slate-600"
                : "border-violet-500/50 bg-violet-950/40 text-violet-100 shadow-[0_0_16px_rgba(139,92,246,0.2)] hover:bg-violet-900/50"
            )}
          >
            👁 启动影子训练
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => void onStop()}
            className={cn(
              "rounded-lg border px-4 py-2.5 text-sm font-semibold transition-all",
              busy
                ? "cursor-not-allowed border-slate-700 text-slate-600"
                : "border-rose-500/50 bg-rose-950/40 text-rose-200 hover:bg-rose-900/45"
            )}
          >
            🛑 停止浏览器
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => void onTrainingTail()}
            className="rounded-lg border border-fuchsia-500/40 px-4 py-2.5 text-sm font-semibold text-fuchsia-100 hover:bg-fuchsia-950/35"
          >
            📚 训练 JSONL 尾部
          </button>
        </div>

        <p className="text-[11px] leading-relaxed text-cyan-600/75">
          本页<strong className="text-cyan-500/90">不</strong>再直调 Playwright；仅向 L3 投递{" "}
          <span className="font-mono text-cyan-500/80">run-skill</span>。Agent 仅见白名单内的{" "}
          <span className="font-mono text-cyan-500/80">mcp:tool_*</span>
          （进程内与 SSE 同源）。Skill 文件：
          <span className="font-mono text-cyan-500/80"> l3_node/skills/gameqa/{SKILL_AUTO}</span> /{" "}
          <span className="font-mono text-cyan-500/80">{SKILL_SHADOW}</span>
          。并行调试仍可用 <span className="font-mono">python -m l3_client.local_mcps.gameqa_mcp.server</span>。
        </p>
      </section>

      <section className="flex min-h-0 flex-1 flex-col gap-2">
        <div className="text-[11px] font-medium uppercase tracking-[0.2em] text-cyan-600/75">
          # MIND STREAM :: GAME QA
        </div>
        <div
          className={cn(
            "flex min-h-[260px] flex-1 flex-col overflow-y-auto rounded-lg border border-cyan-500/25 p-4",
            "bg-[#0a0e17] font-mono text-sm leading-relaxed text-cyan-300",
            "[text-shadow:0_0_10px_rgba(6,182,212,0.12)]"
          )}
        >
          {logs.length === 0 && (
            <div className="text-cyan-700/65">
              SSE 与 [gameqa] / [gameqa][agent] 日志将出现在此。若 engine 未就绪，run-skill 会在流中提示。
            </div>
          )}
          {logs.map((log, index) => (
            <div key={`${index}-${log.slice(0, 48)}`} className="mb-1 break-words whitespace-pre-wrap">
              {log}
            </div>
          ))}
          <div ref={logsEndRef} />
        </div>
      </section>
    </div>
  );
}
