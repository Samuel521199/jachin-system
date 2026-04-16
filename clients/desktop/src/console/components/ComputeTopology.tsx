/**
 * Compute Topology - Ray 集群可视化：Master 居中，Worker 环绕，连线与数据流动画
 */

import { useMemo, useState, useEffect, useCallback } from "react";
import { useDesktopUiLang } from "../../hooks/useDesktopUiLang";
import { getDesktopConsole } from "../../utils/desktopUiI18n";
import { motion, AnimatePresence } from "framer-motion";
import { cn } from "../../utils/cn";
import { getInferenceStrategy, setInferenceStrategy } from "../../lib/api";
import type { GpuStatsItem, ClusterNodeInfo, ClusterTaskInfo } from "../../lib/api";

const MASTER_R = 28;
const WORKER_R = 20;
const VIEW_SIZE = 220;
const CENTER = VIEW_SIZE / 2;

/** Worker 围绕 Master 的分布角度（弧度） */
function workerPosition(index: number, total: number): { x: number; y: number } {
  const step = (2 * Math.PI) / Math.max(1, total);
  const angle = -Math.PI / 2 + index * step;
  const r = 72;
  return {
    x: CENTER + r * Math.cos(angle),
    y: CENTER + r * Math.sin(angle),
  };
}

export function ComputeTopology({
  workerCount = 2,
  activeWorkerIndex = -1,
  cpuPercent = 0,
  ramPercent = 0,
  gpuStats,
  nodes = [],
  tasks = [],
  className,
  /** Dashboard 紧凑带：缩 SVG/间距，适配较低中区高度 */
  compact = false,
}: {
  workerCount?: number;
  activeWorkerIndex?: number;
  cpuPercent?: number;
  ramPercent?: number;
  gpuStats?: GpuStatsItem[];
  /** 集群节点详情 */
  nodes?: ClusterNodeInfo[];
  /** 集群任务详情 */
  tasks?: ClusterTaskInfo[];
  className?: string;
  compact?: boolean;
}) {
  const [lang] = useDesktopUiLang();
  const c = useMemo(() => getDesktopConsole(lang), [lang]);
  const strategyOptions = useMemo(
    () =>
      [
        { id: "eco", label: c.topology.strategyEco },
        { id: "default", label: c.topology.strategyDefault },
        { id: "performance", label: c.topology.strategyPerformance },
        { id: "god", label: c.topology.strategyGod },
      ] as const,
    [c],
  );
  const [showDetails, setShowDetails] = useState(false);
  const [strategyMode, setStrategyMode] = useState<string>("default");

  const fetchStrategy = useCallback(async () => {
    try {
      const res = await getInferenceStrategy();
      if (res?.mode) setStrategyMode(res.mode);
    } catch {
      setStrategyMode("default");
    }
  }, []);

  useEffect(() => {
    fetchStrategy();
  }, [fetchStrategy]);

  const handleStrategyChange = useCallback(async (mode: string) => {
    try {
      await setInferenceStrategy(mode);
      setStrategyMode(mode);
    } catch (e) {
      console.error("setInferenceStrategy failed:", e);
    }
  }, []);

  const workers = useMemo(
    () => Array.from({ length: workerCount }, (_, i) => ({ i, ...workerPosition(i, workerCount) })),
    [workerCount]
  );

  return (
    <div className={cn("flex w-full flex-col items-center", compact ? "gap-1" : "gap-3", className)}>
      <h2
        className={cn(
          "w-full shrink-0 text-center font-mono font-semibold uppercase text-cyan-600/90 [text-shadow:0_0_12px_rgba(6,182,212,0.2)]",
          compact ? "text-[10px] tracking-[0.2em]" : "text-xs tracking-[0.28em]"
        )}
        style={{ fontFamily: "Orbitron, sans-serif" }}
      >
        Compute Topology
      </h2>
      <div
        className={cn(
          "relative flex w-full shrink-0 items-center justify-center",
          compact ? "max-w-[168px]" : "max-w-[240px]"
        )}
      >
        <svg
          viewBox={`0 0 ${VIEW_SIZE} ${VIEW_SIZE}`}
          className={cn(
            "h-auto w-full",
            compact ? "max-h-[118px] max-w-[168px]" : "max-h-[220px] max-w-[240px]"
          )}
          aria-hidden
        >
          <defs>
            <filter id="glow-cyan">
              <feGaussianBlur stdDeviation="2" result="blur" />
              <feMerge>
                <feMergeNode in="blur" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
            <filter id="glow-rose">
              <feGaussianBlur stdDeviation="2" result="blur" />
              <feMerge>
                <feMergeNode in="blur" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
          </defs>
          {/* 连线 Master → Worker */}
          {workers.map(({ i, x, y }) => (
            <line
              key={`line-${i}`}
              x1={CENTER}
              y1={CENTER}
              x2={x}
              y2={y}
              stroke="rgba(255,255,255,0.12)"
              strokeWidth="1"
              strokeDasharray="4 3"
            />
          ))}
          {/* 数据流动画：沿连线 stroke-dashoffset 动画 */}
          {activeWorkerIndex >= 0 && activeWorkerIndex < workers.length && (() => {
            const w = workers[activeWorkerIndex];
            const len = Math.hypot(w.x - CENTER, w.y - CENTER);
            return (
              <line
                x1={CENTER}
                y1={CENTER}
                x2={w.x}
                y2={w.y}
                stroke="rgba(34, 211, 238, 0.6)"
                strokeWidth="2"
                strokeLinecap="round"
                strokeDasharray={`${len} ${len}`}
                filter="url(#glow-cyan)"
              >
                <animate
                  attributeName="stroke-dashoffset"
                  from={len}
                  to={0}
                  dur="1.2s"
                  repeatCount="indefinite"
                />
              </line>
            );
          })()}
          {/* Worker 节点 */}
          {workers.map(({ i, x, y }) => {
            const active = activeWorkerIndex === i;
            return (
              <g key={`worker-${i}`}>
                {active && (
                  <circle
                    cx={x}
                    cy={y}
                    r={WORKER_R + 4}
                    fill="none"
                    stroke="rgba(34, 211, 238, 0.4)"
                    strokeWidth="1"
                    filter="url(#glow-cyan)"
                  >
                    <animate
                      attributeName="opacity"
                      values="0.4;0.8;0.4"
                      dur="1.5s"
                      repeatCount="indefinite"
                    />
                  </circle>
                )}
                <circle
                  cx={x}
                  cy={y}
                  r={WORKER_R}
                  fill={active ? "rgba(34, 211, 238, 0.25)" : "rgba(34, 211, 238, 0.08)"}
                  stroke={active ? "rgba(34, 211, 238, 0.7)" : "rgba(34, 211, 238, 0.35)"}
                  strokeWidth="1.5"
                />
                <text
                  x={x}
                  y={y + 1}
                  textAnchor="middle"
                  className="font-mono text-[10px] fill-cyan-400/90"
                  style={{ fontSize: "10px", fill: "rgba(34, 211, 238, 0.95)" }}
                >
                  W{i + 1}
                </text>
              </g>
            );
          })}
          {/* Master 节点 */}
          <circle
            cx={CENTER}
            cy={CENTER}
            r={MASTER_R}
            fill="rgba(244, 63, 94, 0.15)"
            stroke="rgba(244, 63, 94, 0.5)"
            strokeWidth="2"
          />
          <text
            x={CENTER}
            y={CENTER + 1}
            textAnchor="middle"
            className="font-mono text-xs"
            style={{ fontSize: "11px", fill: "rgba(251, 113, 133, 0.95)" }}
          >
            Master
          </text>
        </svg>
      </div>
      <p
        className={cn(
          "w-full shrink-0 text-center font-mono uppercase tracking-wider text-slate-500",
          compact ? "max-w-[220px] text-[9px]" : "max-w-[260px] text-[10px]"
        )}
      >
        Ray Cluster · {workerCount + 1} nodes
        {gpuStats?.length ? ` · ${gpuStats.length} GPU${gpuStats.length > 1 ? "s" : ""}` : " · 1 GPU"}
      </p>
      <div className={cn("flex w-full shrink-0 flex-col px-1", compact ? "max-w-[220px] gap-1" : "max-w-[260px] gap-2")}>
        <div className="flex w-full items-center gap-2 font-mono">
          <span className={cn("shrink-0 uppercase tracking-wider text-slate-500", compact ? "text-[9px]" : "text-[10px]")}>
            CPU
          </span>
          <span className="h-px min-w-[12px] flex-1 border-b border-dotted border-cyan-500/40 opacity-90" aria-hidden />
          <span
            className={cn(
              "shrink-0 tabular-nums leading-none text-rose-400 drop-shadow-[0_0_10px_rgba(251,113,133,0.35)]",
              compact ? "text-sm" : "text-lg"
            )}
          >
            {cpuPercent}%
          </span>
        </div>
        <div className="flex w-full items-center gap-2 font-mono">
          <span className={cn("shrink-0 uppercase tracking-wider text-slate-500", compact ? "text-[9px]" : "text-[10px]")}>
            RAM
          </span>
          <span className="h-px min-w-[12px] flex-1 border-b border-dotted border-cyan-500/40 opacity-90" aria-hidden />
          <span
            className={cn(
              "shrink-0 tabular-nums leading-none text-cyan-400 drop-shadow-[0_0_10px_rgba(34,211,238,0.3)]",
              compact ? "text-sm" : "text-lg"
            )}
          >
            {ramPercent}%
          </span>
        </div>
        {gpuStats?.length ? (
          <div className="flex w-full items-center gap-2 font-mono">
            <span className={cn("shrink-0 uppercase tracking-wider text-slate-500", compact ? "text-[9px]" : "text-[10px]")}>
              GPU
            </span>
            <span className="h-px min-w-[12px] flex-1 border-b border-dotted border-cyan-500/40 opacity-90" aria-hidden />
            <span
              className={cn(
                "shrink-0 tabular-nums leading-none text-amber-400 drop-shadow-[0_0_10px_rgba(251,191,36,0.35)]",
                compact ? "text-sm" : "text-lg"
              )}
            >
              {gpuStats[0].utilization_gpu ?? 0}%
            </span>
          </div>
        ) : null}
      </div>
      <div
        className={cn(
          "flex w-full shrink-0 flex-wrap items-center justify-center",
          compact ? "mt-0 max-w-[240px] gap-1" : "mt-1 max-w-[280px] gap-1.5"
        )}
      >
        <span
          className={cn(
            "self-center font-mono uppercase tracking-wider text-slate-500",
            compact ? "text-[9px]" : "text-[10px]"
          )}
        >
          {c.topology.runMode}
        </span>
        {strategyOptions.map((opt) => (
          <button
            key={opt.id}
            type="button"
            onClick={() => handleStrategyChange(opt.id)}
            className={cn(
              "border font-mono uppercase tracking-wider transition-all",
              "[clip-path:polygon(4px_0,calc(100%-4px)_0,100%_4px,100%_calc(100%-4px),calc(100%-4px)_100%,4px_100%,0_calc(100%-4px),0_4px)]",
              compact ? "px-1.5 py-0.5 text-[9px]" : "px-2.5 py-1 text-[10px]",
              strategyMode === opt.id
                ? "border-cyan-400/60 bg-cyan-500/20 text-cyan-300 shadow-[0_0_16px_rgba(34,211,238,0.25),inset_0_0_12px_rgba(6,182,212,0.2)]"
                : "border-cyan-500/20 bg-black/40 text-slate-500 hover:border-cyan-400/40 hover:text-slate-300"
            )}
          >
            {opt.label}
          </button>
        ))}
      </div>
      {(nodes.length > 0 || tasks.length > 0) && (
        <div className={cn("w-full", compact ? "mt-1" : "mt-2")}>
          <button
            type="button"
            onClick={() => setShowDetails(!showDetails)}
            className="text-[10px] text-slate-500 hover:text-cyan-400 font-mono transition-colors"
          >
            {showDetails ? c.topology.collapse : c.topology.expandDetails}
          </button>
          <AnimatePresence>
            {showDetails && (
              <motion.div
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: "auto", opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                className="overflow-hidden"
              >
                <div className="mt-2 space-y-2 max-h-28 overflow-y-auto custom-scrollbar text-[10px] font-mono">
                  {nodes.length > 0 && (
                    <div>
                      <span className="text-slate-500">{c.topology.nodesLabel}</span>
                      {nodes.map((n) => (
                        <div key={n.node_id} className="text-slate-400 pl-2">
                          {n.node_type === "master" ? "M" : "W"} · {n.node_id?.slice(0, 8) ?? "—"} · {n.host || "local"} · {n.status}
                        </div>
                      ))}
                    </div>
                  )}
                  {tasks.filter((t) => t.status === "running").length > 0 && (
                    <div>
                      <span className="text-slate-500">{c.topology.runningLabel}</span>
                      {tasks
                        .filter((t) => t.status === "running")
                        .slice(0, 5)
                        .map((t) => (
                          <div key={t.task_id} className="text-cyan-400/90 pl-2">
                            {t.task_type} · {t.skill_id || "-"}
                            {t.worker_node ? ` @ ${t.worker_node.slice(0, 8)}` : ""}
                          </div>
                        ))}
                    </div>
                  )}
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      )}
    </div>
  );
}
