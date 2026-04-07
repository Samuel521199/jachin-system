/**
 * Jachin Core — 流光核心：Sensory 状态机驱动的赛博语义光环
 */

import React from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Terminal, Database, Brain } from "lucide-react";
import type { CoreVisualState, ToolFlashKind } from "../../hooks/useJachinCoreState";

export interface JachinCoreProps {
  state: CoreVisualState;
  toolFlash: ToolFlashKind;
  className?: string;
}

export const JachinCore: React.FC<JachinCoreProps> = ({ state, toolFlash, className = "" }) => {
  const thinking = state === "thinking";

  return (
    <div className={`relative h-11 w-11 flex-shrink-0 ${className}`} aria-hidden>
      <div className="relative h-full w-full flex items-center justify-center">
        {/* 底层：仅光环旋转，图标不跟转 */}
        {thinking && (
          <motion.div
            className="pointer-events-none absolute inset-0 rounded-full"
            style={{
              background:
                "conic-gradient(from 0deg, rgba(34,211,238,0.65), rgba(167,139,250,0.75), rgba(244,63,94,0.35), rgba(34,211,238,0.65))",
            }}
            animate={{ rotate: 360 }}
            transition={{ duration: 1.35, repeat: Infinity, ease: "linear" }}
          />
        )}

        <motion.div
          className={`absolute inset-[2px] rounded-full z-[1] border-2 backdrop-blur-md ${
            state === "hitl"
              ? "border-red-500/70 bg-red-950/50 shadow-[0_0_20px_rgba(239,68,68,0.45)]"
              : state === "self_heal"
                ? "border-amber-500/70 bg-amber-950/35 shadow-[0_0_18px_rgba(245,158,11,0.35)]"
                : state === "streaming"
                  ? "border-cyan-400/40 bg-slate-950/70 shadow-[0_0_14px_rgba(34,211,238,0.25)]"
                  : thinking
                    ? "border-cyan-400/30 bg-slate-950/85"
                    : "border-cyan-500/50 bg-slate-950/80"
          }`}
          animate={
            state === "idle"
              ? {
                  scale: [1, 1.05, 1],
                  boxShadow: [
                    "0 0 10px rgba(0,240,255,0.2)",
                    "0 0 20px rgba(0,240,255,0.6)",
                    "0 0 10px rgba(0,240,255,0.2)",
                  ],
                }
              : state === "hitl"
                ? { scale: [1, 1.06, 1], opacity: [1, 0.72, 1] }
                : state === "streaming"
                  ? { opacity: [0.85, 1, 0.85] }
                  : state === "thinking"
                    ? { scale: [1, 1.1, 1], opacity: [0.9, 1, 0.9] }
                    : state === "self_heal"
                      ? { x: [0, -4, 4, -2, 0] }
                      : {}
          }
          transition={
            state === "idle"
              ? { duration: 3, repeat: Infinity, ease: "easeInOut" }
              : state === "hitl"
                ? { duration: 0.5, repeat: Infinity, ease: "easeInOut" }
                : state === "streaming"
                  ? { duration: 1.6, repeat: Infinity, ease: "easeInOut" }
                  : state === "thinking"
                    ? { duration: 0.55, repeat: Infinity, ease: "easeInOut" }
                    : state === "self_heal"
                      ? { duration: 0.4 }
                      : { duration: 0.2 }
          }
        />

        {state === "idle" && !toolFlash && (
          <motion.div
            className="absolute inset-0 z-0 rounded-full border border-cyan-400/20"
            animate={{ scale: [1, 1.12, 1], opacity: [0.25, 0.45, 0.25] }}
            transition={{ duration: 2.5, repeat: Infinity, ease: "easeInOut" }}
          />
        )}

        <div className="relative z-[2] flex items-center justify-center h-full w-full">
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
