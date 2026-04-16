/**
 * Omni 顶部「动态岛」— 收敛后台任务 / Zombie / 记忆整理 为单一 HUD，展开后操作不变。
 */
import React, { useMemo } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Activity, AlertTriangle, Brain, ChevronDown, ChevronUp, Clock, X } from "lucide-react";
import type {
  BackgroundTaskPulseState,
  MemoryCompactSuggestState,
  ZombieTasksPendingBanner,
} from "../../hooks/useSensoryWebSocket";

export interface OmniDynamicHudProps {
  expanded: boolean;
  onExpandedChange: (v: boolean) => void;
  backgroundTaskPulse: BackgroundTaskPulseState | null;
  zombieTasksPending: ZombieTasksPendingBanner | null;
  dismissZombieTasksPending: () => void;
  memoryCompactSuggest: MemoryCompactSuggestState | null;
  sendMemoryCompactControl: (
    action: "memory_compact_confirm" | "memory_compact_defer" | "memory_compact_cancel",
    hours?: number,
  ) => void;
  dismissMemoryCompactSuggest: () => void;
  setInput: (text: string) => void;
}

export const OmniDynamicHud: React.FC<OmniDynamicHudProps> = ({
  expanded,
  onExpandedChange,
  backgroundTaskPulse,
  zombieTasksPending,
  dismissZombieTasksPending,
  memoryCompactSuggest,
  sendMemoryCompactControl,
  dismissMemoryCompactSuggest,
  setInput,
}) => {
  const zombieActive = zombieTasksPending != null && zombieTasksPending.count > 0;
  const hasAny =
    Boolean(backgroundTaskPulse) || zombieActive || Boolean(memoryCompactSuggest);
  const badgeCount = useMemo(() => {
    let n = 0;
    if (backgroundTaskPulse) n += 1;
    if (zombieActive) n += 1;
    if (memoryCompactSuggest) n += 1;
    return n;
  }, [backgroundTaskPulse, zombieActive, memoryCompactSuggest]);

  if (!hasAny) return null;

  const clipTactical =
    "[clip-path:polygon(8px_0,calc(100%-8px)_0,100%_8px,100%_calc(100%-8px),calc(100%-8px)_100%,8px_100%,0_calc(100%-8px),0_8px)]";

  return (
    <div className="pointer-events-none absolute left-1/2 top-3 z-[45] flex w-full max-w-[min(94vw,32rem)] -translate-x-1/2 flex-col items-center px-2">
      <motion.button
        type="button"
        layout
        onClick={() => onExpandedChange(!expanded)}
        className={`pointer-events-auto flex items-center gap-3 border border-cyan-500/35 bg-black/70 px-5 py-2 font-mono text-[10px] uppercase tracking-[0.28em] text-cyan-100/90 shadow-[0_0_18px_rgba(6,182,212,0.35),inset_0_0_20px_rgba(6,182,212,0.12)] backdrop-blur-md transition hover:border-amber-400/50 hover:shadow-[0_0_26px_rgba(251,191,36,0.25),inset_0_0_24px_rgba(6,182,212,0.18)] ${clipTactical}`}
        aria-expanded={expanded}
        aria-label="战术 HUD"
      >
        <span className="relative flex h-2 w-2 shrink-0">
          <span
            className={`absolute inline-flex h-full w-full animate-ping bg-amber-400/60 ${backgroundTaskPulse ? "opacity-80" : "opacity-30"}`}
            style={{ clipPath: "polygon(50%_0,100%_50%,50%_100%,0_50%)" }}
          />
          <span
            className="relative inline-flex h-2 w-2 bg-cyan-400 shadow-[0_0_10px_rgba(34,211,238,0.9)]"
            style={{ clipPath: "polygon(50%_0,100%_50%,50%_100%,0_50%)" }}
          />
        </span>
        <span className="text-cyan-200/95">TAC</span>
        {badgeCount > 1 ? (
          <span className="flex min-h-[1.25rem] min-w-[1.25rem] items-center justify-center border border-rose-500/80 bg-rose-950/90 px-1 text-[10px] font-bold text-rose-100 shadow-[0_0_14px_rgba(244,63,94,0.55)] animate-pulse">
            {badgeCount}
          </span>
        ) : zombieActive ? (
          <span className="flex min-h-[1.25rem] min-w-[1.25rem] items-center justify-center border border-rose-500/80 bg-rose-950/90 px-1 text-[10px] font-bold text-rose-100 shadow-[0_0_14px_rgba(244,63,94,0.55)] animate-pulse">
            {zombieTasksPending!.count}
          </span>
        ) : memoryCompactSuggest ? (
          <span className="h-2 w-2 border border-amber-400 bg-amber-400/90 shadow-[0_0_12px_rgba(251,191,36,0.7)] animate-pulse" />
        ) : null}
        {expanded ? (
          <ChevronUp className="h-3.5 w-3.5 text-cyan-400/80" strokeWidth={2.5} />
        ) : (
          <ChevronDown className="h-3.5 w-3.5 text-cyan-400/80" strokeWidth={2.5} />
        )}
      </motion.button>

      <AnimatePresence>
        {expanded && (
          <motion.div
            key="hud-panel"
            initial={{ opacity: 0, y: -6, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -4, scale: 0.98 }}
            transition={{ type: "spring", stiffness: 420, damping: 28 }}
            className={`pointer-events-auto mt-2 w-full max-h-[min(52vh,22rem)] overflow-y-auto border border-cyan-500/25 bg-black/75 p-3 font-mono shadow-[0_0_32px_rgba(6,182,212,0.2),inset_0_0_24px_rgba(6,182,212,0.08)] backdrop-blur-md ${clipTactical}`}
          >
            {backgroundTaskPulse && (
              <section
                className={`mb-3 border border-emerald-500/40 bg-emerald-950/40 px-3 py-2.5 text-[11px] text-emerald-100/95 shadow-[0_0_16px_rgba(16,185,129,0.15),inset_0_0_12px_rgba(16,185,129,0.06)] ${clipTactical}`}
                role="status"
                aria-live="polite"
              >
                <div className="mb-1 flex items-center gap-2 font-medium text-emerald-200/95">
                  <Activity className="h-3.5 w-3.5 shrink-0" strokeWidth={2.2} />
                  后台任务
                  <code className="ml-auto border border-emerald-500/30 bg-black/50 px-1.5 py-0.5 text-[10px] text-emerald-200/90">
                    {backgroundTaskPulse.taskId}
                  </code>
                </div>
                <div className="min-h-[1.1em] font-mono text-[12px] tracking-wide text-emerald-300/90">
                  {backgroundTaskPulse.line.length > 0 ? backgroundTaskPulse.line : "\u00a0"}
                </div>
              </section>
            )}

            {zombieActive && (
              <section
                className={`mb-3 border border-rose-500/50 bg-rose-950/45 px-3 py-2.5 text-xs text-rose-100 shadow-[0_0_20px_rgba(244,63,94,0.2),inset_0_0_14px_rgba(244,63,94,0.06)] ${clipTactical}`}
              >
                <p className="mb-1.5 flex flex-wrap items-center gap-2 font-medium text-rose-50">
                  <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-rose-300" />
                  未闭环后台任务
                  <span className="ml-auto border border-rose-400/60 bg-rose-600 px-2 py-0.5 text-[10px] font-bold text-rose-50 shadow-[0_0_12px_rgba(244,63,94,0.45)]">
                    {zombieTasksPending!.count}
                  </span>
                </p>
                <ul className="mb-2 max-h-24 overflow-y-auto text-[11px] leading-snug text-rose-100/90">
                  {zombieTasksPending!.tasks.slice(0, 6).map((t, i) => (
                    <li
                      key={`${t.task_id ?? "t"}-${i}`}
                      className="truncate border-b border-rose-500/10 py-0.5 font-mono last:border-b-0"
                    >
                      <span className="text-rose-200/90">{t.task_id ?? "?"}</span>
                      {(t.task_prompt ?? "").trim()
                        ? ` — ${(t.task_prompt ?? "").slice(0, 80)}${(t.task_prompt ?? "").length > 80 ? "…" : ""}`
                        : ""}
                    </li>
                  ))}
                </ul>
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    className="border border-rose-400/50 bg-transparent px-2.5 py-1.5 text-[11px] font-medium text-rose-100 shadow-[0_0_14px_rgba(244,63,94,0.25)] hover:bg-rose-500/20"
                    onClick={() => {
                      setInput(
                        "请调用 core:check_interrupted_tasks，列出上次断电遗留的后台任务摘要，并问我是否需要用 core:submit_background_task 重新排队执行。",
                      );
                      dismissZombieTasksPending();
                    }}
                  >
                    填入追问
                  </button>
                  <button
                    type="button"
                    className="border border-white/15 bg-white/5 px-2.5 py-1.5 text-[11px] text-rose-100/90 hover:bg-white/10"
                    onClick={() => dismissZombieTasksPending()}
                  >
                    知道了
                  </button>
                </div>
              </section>
            )}

            {memoryCompactSuggest && (
              <section
                className={`border border-amber-500/45 bg-amber-950/50 px-3 py-2.5 text-xs text-amber-100 shadow-[0_0_18px_rgba(245,158,11,0.18),inset_0_0_12px_rgba(245,158,11,0.06)] ${clipTactical}`}
              >
                <p className="mb-1 flex items-center gap-2 font-medium text-amber-50">
                  <Brain className="h-3.5 w-3.5 shrink-0 text-amber-300" />
                  记忆整理
                </p>
                <p className="mb-2 leading-relaxed text-amber-100/90">{memoryCompactSuggest.content}</p>
                <div className="mb-2 flex items-center gap-2 text-[11px] text-amber-200/85">
                  <Clock className="h-3 w-3 shrink-0 opacity-70" />
                  {memoryCompactSuggest.remainingSec > 0
                    ? `${memoryCompactSuggest.remainingSec} 秒后自动开始…`
                    : "正在请求启动…"}
                </div>
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    className="border border-amber-400/60 bg-amber-500/20 px-2.5 py-1.5 text-[11px] font-medium text-amber-50 shadow-[0_0_12px_rgba(251,191,36,0.2)] hover:bg-amber-500/35"
                    onClick={() => sendMemoryCompactControl("memory_compact_confirm")}
                  >
                    立即开始
                  </button>
                  <button
                    type="button"
                    className="border border-white/15 bg-white/5 px-2.5 py-1.5 text-[11px] text-amber-100 hover:bg-white/10"
                    onClick={() => sendMemoryCompactControl("memory_compact_defer", 24)}
                  >
                    推迟 24h
                  </button>
                  <button
                    type="button"
                    className="border border-transparent p-1.5 text-amber-300/80 hover:border-amber-500/30 hover:bg-white/5 hover:text-amber-100"
                    title="关闭"
                    onClick={() => dismissMemoryCompactSuggest()}
                  >
                    <X className="h-4 w-4" strokeWidth={2} />
                  </button>
                </div>
              </section>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
};

export default OmniDynamicHud;
