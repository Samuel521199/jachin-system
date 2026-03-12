/**
 * SwarmRadar - 边缘算力蜂巢雷达
 * v8.0 视觉觉醒：主脑挂起分发重载任务时 3D 旋转扫描，TASK_RESULT 时绿色波纹爆发
 */

import { motion, AnimatePresence } from "framer-motion";

export interface SwarmRadarProps {
  /** offer: 扫描中 | completed: 爆发完成 */
  state: "idle" | "offer" | "assigned" | "completed";
  tool?: string;
  onComplete?: () => void;
}

export function SwarmRadar({ state, tool }: SwarmRadarProps) {
  if (state === "idle") return null;

  return (
    <AnimatePresence mode="wait">
      <motion.div
        key={state}
        className="fixed top-4 right-4 z-40 flex flex-col items-end gap-2"
        initial={{ opacity: 0, scale: 0.8 }}
        animate={{ opacity: 1, scale: 1 }}
        exit={{ opacity: 0, scale: 0.8 }}
        transition={{ duration: 0.2 }}
      >
        {/* 蜂巢雷达图标：扫描时旋转，完成时绿色爆发 */}
        <div
          className={`relative flex items-center justify-center w-12 h-12 rounded-xl border-2 ${
            state === "completed"
              ? "border-emerald-500/80 bg-emerald-500/20 shadow-[0_0_30px_rgba(34,197,94,0.6)]"
              : "border-amber-500/50 bg-amber-500/10"
          }`}
          style={
            state === "completed"
              ? {}
              : { animation: "swarm-radar-scan 2s linear infinite" }
          }
        >
          <span className="text-2xl" aria-hidden>
            🐝
          </span>
          {/* 完成时绿色波纹爆发 */}
          {state === "completed" && (
            <motion.div
              className="absolute inset-0 rounded-xl border-2 border-emerald-400 pointer-events-none"
              initial={{ scale: 1, opacity: 1 }}
              animate={{ scale: 2.5, opacity: 0 }}
              transition={{ duration: 0.8, ease: "easeOut" }}
              style={{ boxShadow: "0 0 40px rgba(34,197,94,0.8)" }}
            />
          )}
        </div>

        {/* 提示文案 */}
        <div
          className={`text-xs font-mono px-2 py-1 rounded ${
            state === "completed"
              ? "bg-emerald-500/30 text-emerald-300 border border-emerald-500/40"
              : "bg-amber-500/20 text-amber-300 border border-amber-500/30"
          }`}
        >
          {state === "completed"
            ? "🐝 算力节点已完成协同计算"
            : `蜂巢扫描中${tool ? ` · ${tool}` : ""}`}
        </div>
      </motion.div>
    </AnimatePresence>
  );
}
