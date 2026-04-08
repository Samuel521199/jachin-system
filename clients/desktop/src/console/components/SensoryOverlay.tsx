/**
 * SensoryOverlay - Layer 3 全息感官投射
 * 连接 ws://localhost:18981/sensory，将大脑脑电波具象化为赛博朋克视觉
 * - thought: 黄色「思考中」光环
 * - core:shell_exec: 红色「物理授权」警告
 * - HITL_REQUIRED: 霸气拦截框「指挥官，是否授权执行？」
 * - v8.0 Handoff: 虫群接力人格切换 Toast
 * - v8.0 Swarm: 蜂巢雷达
 *
 * 重要：若父组件已传入 sensory（如 chat.tsx），则**不得**再调用 useSensoryWebSocket，
 * 否则会多开 WebSocket，L3 只向发起 intent 的那条连接推 chunk，导致其它实例永远收不到流式字。
 */

import { motion, AnimatePresence } from "framer-motion";
import { useSensoryWebSocket } from "../../hooks/useSensoryWebSocket";
import { HandoffToast } from "../../components/HandoffToast";
import { SwarmRadar } from "../../components/SwarmRadar";

export type SensoryOverlayBundle = ReturnType<typeof useSensoryWebSocket>;

export interface SensoryOverlayProps {
  /** 传入则复用同一 WebSocket（如 Chat / MIND STREAM）；不传则本组件自建连接（控制台） */
  sensory?: SensoryOverlayBundle;
  /**
   * minimal：仅保留 Handoff + Swarm；不画全屏思考环 / Shell 横幅 / 全屏 HITL（由 Omni-Bar 承载）
   */
  variant?: "full" | "minimal";
}

/** 仅渲染：避免与 Chat 共用 hook 时再开第二条 /sensory 连接 */
function SensoryOverlayBody({
  sensory,
  variant = "full",
}: {
  sensory: SensoryOverlayBundle;
  variant?: "full" | "minimal";
}) {
  const {
    connected,
    lastPayload,
    hitlPending,
    resolveHitl,
    handoffEvent,
    swarmEvent,
  } = sensory;

  const minimal = variant === "minimal";

  const isThinking = connected && lastPayload?.step_type === "thought";
  const isShellExec =
    connected &&
    lastPayload?.step_type === "action" &&
    (lastPayload?.content?.includes("core:shell_exec") ?? false);

  return (
    <>
      {/* 思考中 - 黄色光环 */}
      <AnimatePresence>
        {!minimal && isThinking && (
          <motion.div
            key="thinking"
            className="fixed inset-0 pointer-events-none z-30 flex items-center justify-center"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.3 }}
          >
            <div className="absolute inset-0 bg-amber-500/5" />
            <motion.div
              className="relative flex flex-col items-center gap-3"
              animate={{
                scale: [1, 1.05, 1],
                opacity: [0.8, 1, 0.8],
              }}
              transition={{
                duration: 2,
                repeat: Infinity,
                ease: "easeInOut",
              }}
            >
              <div className="w-24 h-24 rounded-full border-2 border-amber-400/60 bg-amber-500/10 shadow-[0_0_40px_rgba(251,191,36,0.4)]" />
              <span
                className="text-amber-400 font-bold text-lg tracking-widest"
                style={{ fontFamily: "Orbitron, sans-serif" }}
              >
                思考中
              </span>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* core:shell_exec - 红色物理授权警告 */}
      <AnimatePresence>
        {!minimal && isShellExec && (
          <motion.div
            key="shell-exec"
            className="fixed top-20 left-1/2 -translate-x-1/2 z-30 px-6 py-4 rounded-xl border-2 border-rose-500/80 bg-rose-950/90 shadow-[0_0_30px_rgba(244,63,94,0.5)]"
            initial={{ opacity: 0, y: -20 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -20 }}
            transition={{ duration: 0.25 }}
          >
            <div className="flex items-center gap-3">
              <span className="text-2xl">⚠️</span>
              <div>
                <p
                  className="text-rose-300 font-bold"
                  style={{ fontFamily: "Orbitron, sans-serif" }}
                >
                  物理授权
                </p>
                <p className="text-rose-200/80 text-sm mt-0.5 font-mono truncate max-w-md">
                  {lastPayload?.content ?? "Shell 命令待执行"}
                </p>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* v8.0 Handoff - 虫群接力人格切换 Toast */}
      {handoffEvent?.displayName && (
        <HandoffToast
          displayName={handoffEvent.displayName}
          persona={handoffEvent.persona}
        />
      )}

      {/* v8.0 Swarm - 蜂巢雷达 */}
      <SwarmRadar
        state={
          swarmEvent?.type === "completed"
            ? "completed"
            : swarmEvent?.type === "offer" || swarmEvent?.type === "assigned"
              ? "offer"
              : "idle"
        }
        tool={swarmEvent?.tool}
      />

      {/* HITL_REQUIRED - 霸气拦截框 */}
      <AnimatePresence>
        {!minimal && hitlPending && (
          <motion.div
            key="hitl"
            className="fixed inset-0 z-50 flex items-center justify-center p-6"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
          >
            <div
              className="absolute inset-0 bg-black/80 backdrop-blur-sm"
              onClick={() => resolveHitl(false)}
            />
            <motion.div
              className="relative w-full max-w-md rounded-2xl border-2 border-rose-500/90 bg-slate-900/95 p-8 shadow-[0_0_60px_rgba(244,63,94,0.3)]"
              initial={{ scale: 0.9, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.9, opacity: 0 }}
              transition={{ type: "spring", damping: 25, stiffness: 300 }}
              onClick={(e) => e.stopPropagation()}
            >
              <div className="text-center space-y-6">
                <div className="flex justify-center">
                  <span className="text-5xl">🛡️</span>
                </div>
                <h2
                  className="text-xl font-bold text-rose-400"
                  style={{ fontFamily: "Orbitron, sans-serif" }}
                >
                  指挥官，是否授权执行？
                </h2>
                <p className="text-slate-400 text-sm font-mono bg-white/5 rounded-lg p-4 text-left max-h-24 overflow-y-auto">
                  {hitlPending.content || "[HITL] 需人工授权"}
                </p>
                <div className="flex gap-4 justify-center">
                  <button
                    type="button"
                    onClick={() => resolveHitl(true)}
                    className="px-6 py-3 rounded-xl bg-emerald-500/30 border border-emerald-500/50 text-emerald-300 font-semibold hover:bg-emerald-500/40 transition-colors"
                  >
                    授权
                  </button>
                  <button
                    type="button"
                    onClick={() => resolveHitl(false)}
                    className="px-6 py-3 rounded-xl bg-slate-700/50 border border-slate-500/50 text-slate-300 font-semibold hover:bg-slate-600/50 transition-colors"
                  >
                    拒绝
                  </button>
                </div>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}

/** 无外部 sensory 时单独建连（例如仅大控制台使用 Overlay） */
function SensoryOverlayWithOwnConnection({ variant }: { variant?: "full" | "minimal" }) {
  const sensory = useSensoryWebSocket();
  return <SensoryOverlayBody sensory={sensory} variant={variant} />;
}

export function SensoryOverlay({ sensory: sensoryProp, variant = "full" }: SensoryOverlayProps = {}) {
  if (sensoryProp) {
    return <SensoryOverlayBody sensory={sensoryProp} variant={variant} />;
  }
  return <SensoryOverlayWithOwnConnection variant={variant} />;
}
