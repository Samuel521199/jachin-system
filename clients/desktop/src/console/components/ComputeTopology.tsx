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
    <div className={cn("flex flex-col items-center gap-3", className)}>
      <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-400" style={{ fontFamily: "Orbitron, sans-serif" }}>
        Compute Topology
      </h2>
      <div className="relative flex items-center justify-center">
        <svg
          viewBox={`0 0 ${VIEW_SIZE} ${VIEW_SIZE}`}
          className="w-full max-w-[240px] h-auto"
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
      <p className="text-[10px] text-slate-500 font-mono">
        Ray Cluster · {workerCount + 1} nodes
        {gpuStats?.length ? ` · ${gpuStats.length} GPU${gpuStats.length > 1 ? "s" : ""}` : " · 1 GPU"}
      </p>
      <div className="flex gap-6">
        <div className="flex flex-col items-center gap-0.5">
          <span className="text-lg font-mono text-rose-400">{cpuPercent}%</span>
          <span className="text-[10px] text-slate-500">CPU</span>
        </div>
        <div className="flex flex-col items-center gap-0.5">
          <span className="text-lg font-mono text-cyan-400">{ramPercent}%</span>
          <span className="text-[10px] text-slate-500">RAM</span>
        </div>
        {gpuStats?.length ? (
          <div className="flex flex-col items-center gap-0.5">
            <span className="text-lg font-mono text-amber-400">
              {gpuStats[0].utilization_gpu ?? 0}%
            </span>
            <span className="text-[10px] text-slate-500">GPU</span>
          </div>
        ) : null}
      </div>
      <div className="flex gap-2 mt-2">
        <span className="text-[10px] text-slate-500 font-mono self-center">{c.topology.runMode}</span>
        {strategyOptions.map((opt) => (
          <button
            key={opt.id}
            type="button"
            onClick={() => handleStrategyChange(opt.id)}
            className={cn(
              "px-2 py-1 rounded text-[10px] font-mono transition-colors",
              strategyMode === opt.id
                ? "bg-cyan-500/30 text-cyan-400 border border-cyan-500/50"
                : "bg-white/5 text-slate-500 border border-white/10 hover:bg-white/10 hover:text-slate-300"
            )}
          >
            {opt.label}
          </button>
        ))}
      </div>
      {(nodes.length > 0 || tasks.length > 0) && (
        <div className="w-full mt-2">
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
