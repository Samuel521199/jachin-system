/**
 * HandoffToast - 虫群接力人格切换系统级通知
 * v8.0 视觉觉醒：当检测到 Persona 切换时，极其醒目的 Toast
 */

import { motion, AnimatePresence } from "framer-motion";

export interface HandoffToastProps {
  displayName: string;
  persona: string;
  onDismiss?: () => void;
}

const PERSONA_STYLES: Record<string, { bg: string; border: string; glow: string }> = {
  default: {
    bg: "bg-cyan-500/20",
    border: "border-cyan-400/60",
    glow: "shadow-[0_0_30px_rgba(34,211,238,0.4)]",
  },
  architect: {
    bg: "bg-violet-500/20",
    border: "border-violet-400/60",
    glow: "shadow-[0_0_30px_rgba(139,92,246,0.4)]",
  },
  researcher: {
    bg: "bg-emerald-500/20",
    border: "border-emerald-400/60",
    glow: "shadow-[0_0_30px_rgba(34,197,94,0.4)]",
  },
};

export function HandoffToast({ displayName, persona, onDismiss }: HandoffToastProps) {
  const style = PERSONA_STYLES[persona] ?? PERSONA_STYLES.default;

  return (
    <AnimatePresence>
      <motion.div
        className={`fixed top-20 left-1/2 -translate-x-1/2 z-50 px-6 py-4 rounded-xl border-2 ${style.bg} ${style.border} ${style.glow}`}
        initial={{ opacity: 0, y: -30, scale: 0.9 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        exit={{ opacity: 0, y: -20 }}
        transition={{ type: "spring", damping: 25, stiffness: 300 }}
      >
        <div className="flex items-center gap-3">
          <span className="text-2xl" aria-hidden>
            🔄
          </span>
          <div>
            <p
              className="font-bold text-white/95 tracking-wide"
              style={{ fontFamily: "Orbitron, sans-serif" }}
            >
              虫群接力触发
            </p>
            <p className="text-sm text-white/80 mt-0.5">
              【{displayName}】已接管大脑
            </p>
          </div>
        </div>
      </motion.div>
    </AnimatePresence>
  );
}
