/**
 * Jachin Core — 流光核心：Sensory 状态机驱动的赛博语义光环
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

  /** HITL / 自愈：保留配色，动效走 IDLE 脉冲 */
  const ringMachine: JachinCoreMachineState =
    state === "hitl" || state === "self_heal" ? "IDLE" : machineState;

  const thinking = ringMachine === "THINKING";

  const ringClass =
    state === "hitl"
      ? "border-solid border-red-500/70 bg-red-950/50"
      : state === "self_heal"
        ? "border-solid border-amber-500/70 bg-amber-950/35"
        : ringMachine === "THINKING"
          ? "border-purple-500 border-t-transparent border-l-purple-400 bg-slate-950/85 shadow-[0_0_20px_rgba(168,85,247,0.6)]"
          : ringMachine === "STREAMING"
            ? "border-solid border-cyan-500/50 bg-slate-950/70"
            : "border-solid border-cyan-500/50 bg-slate-950/80";

  const animate =
    ringMachine === "IDLE"
      ? {
          scale: [1, 1.05, 1],
          opacity: [0.6, 1, 0.6],
          boxShadow: [
            "0 0 10px rgba(0,240,255,0.2)",
            "0 0 18px rgba(0,240,255,0.42)",
            "0 0 10px rgba(0,240,255,0.2)",
          ],
        }
      : ringMachine === "THINKING"
        ? {
            rotate: [0, 360],
            scale: [1, 1.1, 1],
            boxShadow: [
              "0 0 15px rgba(168,85,247,0.5)",
              "0 0 25px rgba(168,85,247,0.8)",
              "0 0 15px rgba(168,85,247,0.5)",
            ],
          }
        : {
            scale: 1,
            opacity: 1,
            boxShadow: "0 0 15px rgba(0,240,255,0.6)",
          };

  const transition =
    ringMachine === "THINKING"
      ? {
          rotate: { duration: 1.5, repeat: Infinity, ease: "linear" },
          scale: { duration: 0.8, repeat: Infinity, ease: "easeInOut" },
          boxShadow: { duration: 0.8, repeat: Infinity, ease: "easeInOut" },
        }
      : { duration: 3, repeat: Infinity, ease: "easeInOut" };

  return (
    <div className={`relative h-11 w-11 flex-shrink-0 ${className}`} aria-hidden>
      <div className="relative h-full w-full flex items-center justify-center">
        <motion.div
          key={ringMachine}
          className={`pointer-events-none absolute inset-0 z-[1] rounded-full border-2 backdrop-blur-md ${ringClass}`}
          animate={animate}
          transition={transition}
        />

        <div className="relative z-[2] flex h-full w-full items-center justify-center">
          <AnimatePresence mode="wait">
            {toolFlash === "terminal" && (
              <motion.div
                key="tf-term"
                className="text-cyan-200"
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
                className="text-violet-200"
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
                className="text-cyan-200/90"
                initial={{ opacity: 0.6, scale: 0.85 }}
                animate={{ opacity: [0.75, 1, 0.75], rotate: [0, 360] }}
                transition={{
                  opacity: { duration: 0.55, repeat: Infinity, ease: "easeInOut" },
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
