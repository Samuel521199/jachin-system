/**
 * 冒烟测试 — K11 统合平台 Playwright 冒烟（子进程，SSE 日志）
 * 后端：L3 `GET /api/v1/k11-unified-smoke/stream` → `python scripts/test_k11_unified_platform_smoke_playwright.py`
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { FlaskConical } from "lucide-react";
import { cn } from "../../utils/cn";
import { getK11P2CompatOnlyStreamUrl, getK11UnifiedSmokeStreamUrl, getL3MonitorApiUrl } from "../../lib/api";

const DEFAULT_TARGET = "https://www.kalaroko.com/";

export function K11UnifiedSmokeTest() {
  const [targetUrl, setTargetUrl] = useState(DEFAULT_TARGET);
  const [cdpHttp, setCdpHttp] = useState("");
  const [verbose, setVerbose] = useState(true);
  const [noLark, setNoLark] = useState(false);
  const [headlessP2, setHeadlessP2] = useState(false);
  const [running, setRunning] = useState(false);
  const [logs, setLogs] = useState<string[]>([]);
  const [doneOk, setDoneOk] = useState<boolean | null>(null);
  const [showNotify, setShowNotify] = useState(false);
  const [exitCode, setExitCode] = useState<number | null>(null);
  const [notifyMsg, setNotifyMsg] = useState("K11 统合冒烟已完成（退出码 0）");
  const logsEndRef = useRef<HTMLDivElement>(null);
  const esRef = useRef<EventSource | null>(null);

  useEffect(
    () => () => {
      esRef.current?.close();
    },
    []
  );

  const handleStop = useCallback(async () => {
    try {
      const url = getL3MonitorApiUrl("/api/v1/k11-unified-smoke/stop");
      const res = await fetch(url, { method: "POST" });
      const ok = res.ok;
      setLogs((prev) => [...prev, ok ? "> 已发送停止信号（子进程将尽快退出）…" : "> 停止请求失败（HTTP）。"]);
    } catch {
      setLogs((prev) => [...prev, "> 停止请求失败（网络）。"]);
    }
  }, []);

  useEffect(() => {
    logsEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs]);

  const handleStart = useCallback(() => {
    esRef.current?.close();
    setNotifyMsg("K11 统合冒烟已完成（退出码 0）");
    setLogs([
      "> 初始化 K11 统合平台冒烟（Playwright）…",
      `> 目标: ${targetUrl.trim() || `（脚本默认 ${DEFAULT_TARGET}）`}`,
      "> 连接 L3 SSE 流…",
    ]);
    setDoneOk(null);
    setExitCode(null);
    setShowNotify(false);

    const u = targetUrl.trim();
    const cdp = cdpHttp.trim();
    const streamUrl = getK11UnifiedSmokeStreamUrl({
      targetUrl: u || undefined,
      cdpHttp: cdp || undefined,
      verbose,
      noLarkReport: noLark,
    });
    const eventSource = new EventSource(streamUrl);
    esRef.current = eventSource;
    setRunning(true);

    eventSource.onmessage = (event: MessageEvent<string>) => {
      try {
        const data = JSON.parse(event.data) as Record<string, unknown>;
        if (data.type === "done") {
          const ok = data.ok === true;
          const cancelled = data.cancelled === true;
          setDoneOk(ok);
          if (typeof data.exit_code === "number") {
            setExitCode(data.exit_code);
          } else {
            setExitCode(null);
          }
          if (cancelled) {
            setLogs((prev) => [...prev, "> █ 冒烟已中断（子进程/连接取消）。"]);
          }
          setLogs((prev) => [...prev, `> █ 任务结束，退出码: ${String(data.exit_code ?? "?")}。`]);
          setRunning(false);
          eventSource.close();
          if (ok) {
            setShowNotify(true);
            window.setTimeout(() => setShowNotify(false), 7000);
          }
          return;
        }
        if (data.type === "error") {
          const msg = typeof data.message === "string" ? data.message : "未知错误";
          setLogs((prev) => [...prev, `> [ERROR] ${msg}`]);
          setRunning(false);
          setDoneOk(false);
          eventSource.close();
          return;
        }
        if (typeof data.line === "string") {
          setLogs((prev) => [...prev, `> ${data.line}`]);
        }
      } catch {
        /* ignore */
      }
    };

    eventSource.onerror = () => {
      setLogs((prev) => [...prev, "> [WARN] 流异常中断（请确认 L3 已启动且 `/l3` 代理可用）。"]);
      setRunning(false);
      setDoneOk(false);
      eventSource.close();
    };
  }, [cdpHttp, noLark, targetUrl, verbose]);

  const handleStartP2CompatOnly = useCallback(() => {
    esRef.current?.close();
    setNotifyMsg("P2 浏览器兼容已完成（退出码 0）");
    setLogs([
      "> 模式：P2 浏览器兼容独立（--only-compat，Chrome/Edge 双通道）…",
      `> 目标: ${targetUrl.trim() || "（脚本默认见 test_k11_p2_compat_weaknet_playwright 文档）"}`,
      "> 等效: python scripts/test_k11_p2_compat_weaknet_playwright.py --only-compat",
      "> 连接 L3 SSE 流…",
    ]);
    setDoneOk(null);
    setExitCode(null);
    setShowNotify(false);

    const u = targetUrl.trim();
    const cdp = cdpHttp.trim();
    const streamUrl = getK11P2CompatOnlyStreamUrl({
      targetUrl: u || undefined,
      cdpHttp: cdp || undefined,
      verbose,
      noLarkReport: noLark,
      headless: headlessP2,
    });
    const eventSource = new EventSource(streamUrl);
    esRef.current = eventSource;
    setRunning(true);

    eventSource.onmessage = (event: MessageEvent<string>) => {
      try {
        const data = JSON.parse(event.data) as Record<string, unknown>;
        if (data.type === "done") {
          const ok = data.ok === true;
          const cancelled = data.cancelled === true;
          setDoneOk(ok);
          if (typeof data.exit_code === "number") {
            setExitCode(data.exit_code);
          } else {
            setExitCode(null);
          }
          if (cancelled) {
            setLogs((prev) => [...prev, "> █ 已中断。"]);
          }
          setLogs((prev) => [...prev, `> █ 任务结束，退出码: ${String(data.exit_code ?? "?")}。`]);
          setRunning(false);
          eventSource.close();
          if (ok) {
            setShowNotify(true);
            window.setTimeout(() => setShowNotify(false), 7000);
          }
          return;
        }
        if (data.type === "error") {
          const msg = typeof data.message === "string" ? data.message : "未知错误";
          setLogs((prev) => [...prev, `> [ERROR] ${msg}`]);
          setRunning(false);
          setDoneOk(false);
          eventSource.close();
          return;
        }
        if (typeof data.line === "string") {
          setLogs((prev) => [...prev, `> ${data.line}`]);
        }
      } catch {
        /* ignore */
      }
    };

    eventSource.onerror = () => {
      setLogs((prev) => [...prev, "> [WARN] 流异常中断（请确认 L3 已启动且 `/l3` 代理可用）。"]);
      setRunning(false);
      setDoneOk(false);
      eventSource.close();
    };
  }, [cdpHttp, headlessP2, noLark, targetUrl, verbose]);

  return (
    <div className="relative flex h-full min-h-0 flex-col gap-5 p-6 text-cyan-300">
      {showNotify && (
        <div
          className={cn(
            "fixed right-6 top-20 z-[100] max-w-sm rounded-lg border px-4 py-3 text-sm backdrop-blur",
            "border-cyan-500/45 bg-black/90 text-cyan-100 shadow-[0_0_28px_rgba(34,211,238,0.18)]"
          )}
          role="status"
        >
          {notifyMsg}
        </div>
      )}

      <header className="flex flex-shrink-0 flex-wrap items-center gap-3 border-b border-cyan-500/15 pb-4">
        <div className="flex h-10 w-10 items-center justify-center rounded-lg border border-cyan-500/25 bg-cyan-500/10">
          <FlaskConical className="h-5 w-5 text-cyan-300" aria-hidden />
        </div>
        <div>
          <h2
            className="font-sci-fi text-lg font-semibold tracking-wide text-white"
            style={{ textShadow: "0 0 12px rgba(34, 211, 238, 0.45)" }}
          >
            ■ K11 统合平台冒烟
          </h2>
          <p className="text-xs text-cyan-700/90">脚本：scripts/test_k11_unified_platform_smoke_playwright.py</p>
        </div>
      </header>

      <section className="flex flex-shrink-0 flex-col gap-4 rounded-xl border border-cyan-500/20 bg-cyan-500/[0.05] p-4">
        <div className="flex flex-wrap items-end gap-4">
          <label className="flex min-w-[220px] max-w-md flex-1 flex-col gap-1 text-xs text-cyan-600/90">
            目标 URL（--target-url，空则由脚本使用默认站）
            <input
              type="url"
              value={targetUrl}
              disabled={running}
              onChange={(e) => setTargetUrl(e.target.value)}
              placeholder={DEFAULT_TARGET}
              className="rounded border border-cyan-500/35 bg-black/60 px-2 py-1.5 font-mono text-cyan-100 outline-none focus:border-cyan-400/60"
            />
          </label>
          <label className="flex min-w-[200px] max-w-md flex-1 flex-col gap-1 text-xs text-cyan-600/90">
            CDP 端点（--cdp-http，可选，覆盖 KALAROKO_CDP_ENDPOINT）
            <input
              type="url"
              value={cdpHttp}
              disabled={running}
              onChange={(e) => setCdpHttp(e.target.value)}
              placeholder="留空用环境/默认"
              className="rounded border border-cyan-500/35 bg-black/60 px-2 py-1.5 font-mono text-sm text-cyan-100 outline-none focus:border-cyan-400/60"
            />
          </label>
        </div>
        <div className="flex flex-wrap items-center gap-6 text-xs text-cyan-600/90">
          <label className="flex cursor-pointer items-center gap-2">
            <input
              type="checkbox"
              checked={verbose}
              disabled={running}
              onChange={(e) => setVerbose(e.target.checked)}
              className="h-3.5 w-3.5"
            />
            详细输出 (-v)
          </label>
          <label className="flex cursor-pointer items-center gap-2" title="脚本 --no-lark-report：不写飞书、不发完成通知">
            <input
              type="checkbox"
              checked={noLark}
              disabled={running}
              onChange={(e) => setNoLark(e.target.checked)}
              className="h-3.5 w-3.5"
            />
            不推送飞书 (--no-lark-report)
          </label>
        </div>
        <div className="ml-auto flex flex-col items-stretch gap-3 sm:items-end">
          <div className="flex flex-wrap items-center justify-end gap-2">
            <button
              type="button"
              disabled={running}
              onClick={handleStart}
              className={cn(
                "rounded-lg px-5 py-2.5 text-sm font-bold transition-all",
                running
                  ? "cursor-not-allowed bg-slate-800 text-slate-500"
                  : "bg-cyan-400 text-black shadow-[0_0_24px_rgba(34,211,238,0.35)] hover:bg-cyan-300"
              )}
            >
              {running ? "执行中…" : "🚀 启动统合冒烟"}
            </button>
            <button
              type="button"
              disabled={running}
              onClick={handleStartP2CompatOnly}
              className={cn(
                "rounded-lg border px-4 py-2.5 text-sm font-bold transition-all",
                running
                  ? "cursor-not-allowed border-slate-700 bg-slate-900/50 text-slate-600"
                  : "border-cyan-500/50 bg-cyan-950/40 text-cyan-100 shadow-[0_0_16px_rgba(34,211,238,0.2)] hover:bg-cyan-900/50"
              )}
              title="等效：test_k11_p2_compat_weaknet_playwright.py --only-compat"
            >
              🧩 仅浏览器兼容
            </button>
            <button
              type="button"
              disabled={!running}
              onClick={() => void handleStop()}
              className={cn(
                "rounded-lg border px-4 py-2.5 text-sm font-semibold transition-all",
                running
                  ? "border-rose-500/50 bg-rose-950/40 text-rose-200 hover:bg-rose-900/50"
                  : "cursor-not-allowed border-slate-700 bg-slate-900/40 text-slate-600"
              )}
            >
              🛑 停止
            </button>
          </div>
          <div className="flex flex-wrap items-center justify-end gap-3 text-[11px] text-cyan-600/80">
            <span className="text-cyan-500/80">P2 兼容段</span>
            <label className="flex cursor-pointer items-center gap-1.5">
              <input
                type="checkbox"
                className="h-3.5 w-3.5"
                checked={headlessP2}
                disabled={running}
                onChange={(e) => setHeadlessP2(e.target.checked)}
              />
              无头 (--headless)
            </label>
          </div>
        </div>
        <p className="text-[11px] leading-relaxed text-cyan-600/75">
          在 L3 本机以子进程跑上述脚本，日志经 SSE 推送到本页。统合冒烟需
          <code className="text-cyan-500/85">KALAROKO_CDP_ENDPOINT</code>
          等；P2 仅「浏览器兼容」段自行起 Chrome/Edge，一般 <strong>不依赖</strong> CDP。飞书表与完成卡与统合脚本共用{" "}
          <code className="text-cyan-500/85">k11_lark_smoke_report</code>。
        </p>
      </section>

      <section className="flex min-h-0 flex-1 flex-col gap-2">
        <div className="text-[11px] font-medium uppercase tracking-[0.2em] text-cyan-600/75"># MIND STREAM :: K11 SMOKE</div>
        <div
          className={cn(
            "flex min-h-[280px] flex-1 flex-col overflow-y-auto rounded-lg border border-cyan-500/25 p-4",
            "bg-[#0a0e17] font-mono text-sm leading-relaxed text-cyan-300",
            "[text-shadow:0_0_10px_rgba(6,182,212,0.12)]"
          )}
        >
          {logs.length === 0 && (
            <div className="text-cyan-700/65">等待启动… 子进程标准输出将在此流式显示。</div>
          )}
          {logs.map((log, index) => (
            <div key={`${index}-${log.slice(0, 48)}`} className="mb-1 break-words whitespace-pre-wrap">
              {log}
            </div>
          ))}
          <div ref={logsEndRef} />
        </div>
      </section>

      {exitCode != null && doneOk === false && (
        <p className="flex-shrink-0 text-sm text-rose-400/95">
          本次任务未通过，退出码 {exitCode}。请根据上方日志与 Playwright/目标站状态排查。
        </p>
      )}
    </div>
  );
}
