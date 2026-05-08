/**
 * 游戏测试 — GameQA（自治测试 + 影子训练）
 * 对接 L3 ``/api/v1/gameqa/*`` 与同进程 Playwright 会话；SSE 合并 MIND STREAM。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Gamepad2 } from "lucide-react";
import { cn } from "../../utils/cn";
import {
  K11_SMOKE_DEFAULT_TARGET_URL,
  getGameQALogStreamUrlAsync,
  getGameQAUrlRaw,
  postGameQAJson,
} from "../../lib/api";

export function GameQAPanel() {
  const [targetUrl, setTargetUrl] = useState(K11_SMOKE_DEFAULT_TARGET_URL.trim());
  const [knowledgePath, setKnowledgePath] = useState("");
  const [elementName, setElementName] = useState("Btn_Call");
  const [logs, setLogs] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [sseOk, setSseOk] = useState<boolean | null>(null);
  const [lastStateJson, setLastStateJson] = useState<string>("");
  const [modeHint, setModeHint] = useState<string>("idle");
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

  const refreshStatus = useCallback(async () => {
    try {
      const url = await getGameQAUrlRaw("status", { bypassCache: true });
      const res = await fetch(url);
      const data = await res.json();
      if (data.mode) setModeHint(String(data.mode));
      pushJsonSnippet("gameqa/status", data);
    } catch (e) {
      appendLogs([`> [ERROR] status: ${e instanceof Error ? e.message : String(e)}`]);
    }
  }, [appendLogs, pushJsonSnippet]);

  const runPost = useCallback(
    async (suffix: string, body: Record<string, unknown>) => {
      setBusy(true);
      try {
        const res = await postGameQAJson(suffix, body, { bypassCache: true });
        const data = await res.json().catch(() => ({ error: "invalid json" }));
        if (!res.ok) pushJsonSnippet(`HTTP ${res.status} ${suffix}`, data);
        else pushJsonSnippet(suffix, data);
        return data as Record<string, unknown>;
      } catch (e) {
        appendLogs([`> [ERROR] ${suffix}: ${e instanceof Error ? e.message : String(e)}`]);
        return null;
      } finally {
        setBusy(false);
      }
    },
    [appendLogs, pushJsonSnippet]
  );

  const onLaunchTest = () => void runPost("launch-test", { url: targetUrl.trim() }).then(refreshStatus);
  const onLaunchShadow = () => void runPost("launch-shadow", { url: targetUrl.trim() }).then(refreshStatus);
  const onStop = () =>
    void runPost("stop", {}).then(async () => {
      setLastStateJson("");
      await refreshStatus();
    });

  const onSemantic = async () => {
    setBusy(true);
    try {
      const res = await postGameQAJson("semantic-state", {}, { bypassCache: true });
      const data = await res.json().catch(() => ({}));
      pushJsonSnippet("semantic-state", data);
      if (data?.ok === true && data?.state) {
        setLastStateJson(JSON.stringify(data.state, null, 2));
        setModeHint(String((data.state as { mode?: string }).mode || modeHint));
      }
    } catch (e) {
      appendLogs([`> ${e instanceof Error ? e.message : String(e)}`]);
    } finally {
      setBusy(false);
    }
  };

  const onExecute = () => void runPost("execute", { element_name: elementName.trim() }).then(refreshStatus);

  const onReadKnowledge = () =>
    void runPost(
      "read-knowledge",
      knowledgePath.trim() ? { file_path: knowledgePath.trim() } : {}
    ).then(refreshStatus);

  const onAudit = useCallback(async () => {
    setBusy(true);
    try {
      const url = await getGameQAUrlRaw("audit", { bypassCache: true });
      const res = await fetch(url);
      const data = await res.json();
      pushJsonSnippet("audit", data);
    } catch (e) {
      appendLogs([`> ${e instanceof Error ? e.message : String(e)}`]);
    } finally {
      setBusy(false);
    }
  }, [appendLogs, pushJsonSnippet]);

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

  const knownKeysPreview = useMemo(() => {
    try {
      const o = JSON.parse(lastStateJson) as { elements?: Record<string, number[]> };
      return o.elements ? Object.keys(o.elements).join(", ") : "";
    } catch {
      return "";
    }
  }, [lastStateJson]);

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
            L3 内 Playwright：{" "}
            <span className="font-mono text-cyan-600/90">/api/v1/gameqa/launch-test | launch-shadow</span>
            · SSE：<span className="font-mono text-cyan-600/90">log-stream</span>
            {sseOk === true && (
              <span className="ml-2 text-emerald-400/90">● SSE 已连接</span>
            )}
            {sseOk === false && <span className="ml-2 text-amber-400/85">● SSE 未连接</span>}
            {" · "}模式 <span className="font-mono text-cyan-500/85">{modeHint}</span>
          </p>
        </div>
        <button
          type="button"
          disabled={busy}
          onClick={() => void refreshStatus()}
          className={cn(
            "rounded-lg border border-cyan-500/45 px-3 py-1.5 text-xs font-semibold",
            busy ? "text-slate-500" : "text-cyan-200 hover:bg-cyan-950/50"
          )}
        >
          刷新状态
        </button>
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
            规则 MD 路径（可空则用仓库默认 tongits_rules.md）
            <input
              type="text"
              value={knowledgePath}
              disabled={busy}
              onChange={(e) => setKnowledgePath(e.target.value)}
              placeholder="留空 → L3 自动解析 knowledge/tongits_rules.md"
              className="w-full rounded border border-cyan-500/35 bg-black/60 px-2 py-1.5 font-mono text-cyan-100 outline-none focus:border-cyan-400/60"
            />
          </label>
        </div>

        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            disabled={busy}
            onClick={() => void onLaunchTest()}
            title="无头 Chromium：自治冒烟 / Agent 闭环"
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
            onClick={() => void onLaunchShadow()}
            title="有头 + 点击劫持 → training_data.jsonl"
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
        </div>

        <div className="flex flex-wrap items-end gap-3 border-t border-cyan-500/15 pt-4">
          <button
            type="button"
            disabled={busy}
            onClick={() => void onSemantic()}
            className="rounded-lg border border-cyan-500/45 bg-black/50 px-4 py-2 text-sm font-semibold text-cyan-100 hover:border-cyan-400/60"
          >
            📷 刷新语义状态
          </button>
          <label className="flex flex-col gap-1 text-xs text-cyan-600/90">
            语义动作名
            <input
              type="text"
              value={elementName}
              disabled={busy}
              onChange={(e) => setElementName(e.target.value)}
              className="w-44 rounded border border-cyan-500/35 bg-black/60 px-2 py-1.5 font-mono text-cyan-100"
            />
          </label>
          <button
            type="button"
            disabled={busy}
            onClick={() => void onExecute()}
            className="mb-0.5 rounded-lg bg-emerald-600/85 px-4 py-2 text-sm font-bold text-black hover:bg-emerald-500"
          >
            ▶ 执行点击
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => void onReadKnowledge()}
            className="mb-0.5 rounded-lg border border-amber-500/45 px-4 py-2 text-sm font-semibold text-amber-100 hover:bg-amber-950/40"
          >
            📖 加载规则 MD
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => void onAudit()}
            className="mb-0.5 rounded-lg border px-4 py-2 text-sm font-semibold text-cyan-200 border-cyan-500/35 hover:bg-cyan-950/30"
          >
            📋 拉取审计日志
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => void onTrainingTail()}
            className="mb-0.5 rounded-lg border border-fuchsia-500/40 px-4 py-2 text-sm font-semibold text-fuchsia-100 hover:bg-fuchsia-950/35"
          >
            📚 训练 JSONL 尾部
          </button>
        </div>

        {knownKeysPreview && (
          <p className="text-[11px] text-cyan-600/80">
            当前语义 keys（最近一次刷新）：{" "}
            <span className="font-mono text-cyan-500/85">{knownKeysPreview}</span>
          </p>
        )}

        <p className="text-[11px] leading-relaxed text-cyan-600/75">
          <strong className="text-cyan-500/90">自治测试</strong>：无头 + 语义表 + 「刷新→执行」闭环；也可用 HTTP/MCP 调同一接口。
          <strong className="text-violet-400/90"> 影子训练</strong>
          ：有头；点击后会 <strong className="text-cyan-500/90">后台截屏 + 对齐语义</strong>，不要求先手点「刷新」。与 MCP/Agent
          同屏：设置相同的 <span className="font-mono text-cyan-500/80">GAMEQA_CDP_URL</span>，或使用{" "}
          <span className="font-mono text-cyan-500/80">~/.gameqa_mcp/cdp_http.txt</span> 中指地址 attach，勿启用{" "}
          <span className="font-mono">GAMEQA_FORCE_NEW_BROWSER=1</span>
          （除非真要新开实例）。默认调试端口可调{" "}
          <span className="font-mono text-cyan-500/80">GAMEQA_REMOTE_DEBUG_PORT</span>
          （默认 9238）。训练落盘：<span className="font-mono text-cyan-500/80">training_data.jsonl</span>
          ，目录由 <span className="font-mono">GAMEQA_DATA_DIR</span> 决定。并行仍可通过{" "}
          <span className="font-mono text-cyan-500/80">python -m l3_client.local_mcps.gameqa_mcp.server</span>{" "}
          给 Cursor 调工具。
        </p>

        {lastStateJson && (
          <div className="max-h-40 overflow-auto rounded border border-cyan-500/20 bg-black/40 p-2 font-mono text-[10px] text-cyan-500/85">
            {lastStateJson}
          </div>
        )}
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
              SSE 已连上后，操作与 [gameqa] 行会流经此处。影子模式可直接在浏览器内点击，懒对齐会写 training JSONL；要与 MCP/Agent
              同屏请共用 CDP（见页底说明）。
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
