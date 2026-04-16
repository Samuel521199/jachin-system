/**
 * Jachin Core — 数字心脏：双层反向旋转环 + 中心光点心跳（Sensory / 工具闪态）
 */

import React from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Terminal, Database, Brain } from "lucide-react";
import type { CoreVisualState, ToolFlashKind } from "../../hooks/useJachinCoreState";

/** 呼吸环显式机位（须由父组件按消息/WS 同步；未传时从 `state` 推导） */
export type JachinCoreMachineState = "IDLE" | "THINKING" | "STREAMING";

export interface JachinCoreProps {
  state: CoreVisualState;
  /** 显式传递，避免仅靠 `state` 时与气泡内 Thought Process 脱节 */
  machineState?: JachinCoreMachineState;
  toolFlash: ToolFlashKind;
  className?: string;
}

export const JachinCore: React.FC<JachinCoreProps> = ({
  state,
  machineState: machineStateProp,
  toolFlash,
  className = "",
}) => {
  const machineState: JachinCoreMachineState =
    machineStateProp ??
    (state === "thinking" ? "THINKING" : state === "streaming" ? "STREAMING" : "IDLE");

  const ringMachine: JachinCoreMachineState =
    state === "hitl" || state === "self_heal" ? "IDLE" : machineState;

  const thinking = ringMachine === "THINKING";
  const streaming = ringMachine === "STREAMING";

  const dashedBorder =
    state === "hitl"
      ? "border-red-400/50"
      : state === "self_heal"
        ? "border-amber-400/45"
        : thinking
          ? "border-amber-400/55"
          : "border-cyan-400/35";

  const thinBorder =
    state === "hitl"
      ? "border-red-400/30"
      : state === "self_heal"
        ? "border-amber-400/25"
        : thinking
          ? "border-amber-300/35"
          : "border-cyan-400/22";

  const heartDuration = thinking ? 0.52 : streaming ? 0.85 : 1.18;
  const heartColor =
    state === "hitl"
      ? "bg-red-400"
      : state === "self_heal"
        ? "bg-amber-400"
        : thinking
          ? "bg-amber-300"
          : "bg-cyan-200";

  const heartShadow =
    state === "hitl"
      ? ["0 0 6px rgba(248,113,113,0.85)", "0 0 18px rgba(248,113,113,1)", "0 0 6px rgba(248,113,113,0.85)"]
      : state === "self_heal"
        ? ["0 0 6px rgba(251,191,36,0.75)", "0 0 16px rgba(245,158,11,0.95)", "0 0 6px rgba(251,191,36,0.75)"]
        : thinking
          ? ["0 0 8px rgba(251,191,36,0.9)", "0 0 22px rgba(251,191,36,1)", "0 0 8px rgba(251,191,36,0.9)"]
          : ["0 0 6px rgba(34,211,238,0.65)", "0 0 16px rgba(34,211,238,0.95)", "0 0 6px rgba(34,211,238,0.65)"];

  const outerRotate = thinking ? 9 : 18;
  const innerRotate = thinking ? 7 : 12;

  return (
    <div className={`relative h-11 w-11 flex-shrink-0 ${className}`} aria-hidden>
      <div className="relative flex h-full w-full items-center justify-center">
        {/* 外层：虚线，顺时针 */}
        <motion.div
          className={`pointer-events-none absolute inset-[-7px] z-0 rounded-full border border-dashed ${dashedBorder}`}
          animate={{ rotate: [0, 360] }}
          transition={{ duration: outerRotate, repeat: Infinity, ease: "linear" }}
        />
        {/* 中层：极细实线，逆时针 */}
        <motion.div
          className={`pointer-events-none absolute inset-[-2px] z-[1] rounded-full border ${thinBorder}`}
          animate={{ rotate: [0, -360] }}
          transition={{ duration: innerRotate, repeat: Infinity, ease: "linear" }}
        />

        {/* 数字心脏：中心光点（思考时由脑图标承载高能态，避免与图标叠糊） */}
        {!toolFlash && !thinking && (
          <motion.div
            className={`pointer-events-none absolute left-1/2 top-1/2 z-[2] h-2 w-2 -translate-x-1/2 -translate-y-1/2 rounded-full ${heartColor}`}
            animate={{
              scale: [1, 1.28, 1],
              opacity: [0.75, 1, 0.78],
              boxShadow: heartShadow,
            }}
            transition={{
              duration: heartDuration,
              repeat: Infinity,
              ease: [0.45, 0, 0.55, 1],
            }}
          />
        )}

        <div className="relative z-[4] flex h-full w-full items-center justify-center">
          <AnimatePresence mode="wait">
            {toolFlash === "terminal" && (
              <motion.div
                key="tf-term"
                className="text-cyan-200 drop-shadow-[0_0_8px_rgba(34,211,238,0.75)]"
                initial={{ opacity: 0, scale: 0.6 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.7 }}
                transition={{ duration: 0.1 }}
              >
                <Terminal className="h-5 w-5" strokeWidth={2.2} />
              </motion.div>
            )}
            {toolFlash === "database" && (
              <motion.div
                key="tf-db"
                className="text-violet-200 drop-shadow-[0_0_8px_rgba(167,139,250,0.7)]"
                initial={{ opacity: 0, scale: 0.6 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.7 }}
                transition={{ duration: 0.1 }}
              >
                <Database className="h-5 w-5" strokeWidth={2.2} />
              </motion.div>
            )}
            {thinking && !toolFlash && (
              <motion.div
                key="core-brain"
                className="text-amber-200/95 drop-shadow-[0_0_10px_rgba(251,191,36,0.65)]"
                initial={{ opacity: 0.6, scale: 0.85 }}
                animate={{ opacity: [0.85, 1, 0.85], rotate: [0, 360] }}
                transition={{
                  opacity: { duration: 0.45, repeat: Infinity, ease: "easeInOut" },
                  rotate: { duration: 8, repeat: Infinity, ease: "linear" },
                }}
              >
                <Brain className="h-5 w-5" strokeWidth={2.2} aria-hidden />
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>
    </div>
  );
};

export default JachinCore;
