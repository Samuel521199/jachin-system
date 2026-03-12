/**
 * InventoryToast - 发现新战略物资时的酷炫 Toast
 * 右下角弹出，极客风格
 */

import { motion, AnimatePresence } from "framer-motion";

export interface InventoryToastProps {
  visible: boolean;
  onDismiss: () => void;
}

export function InventoryToast({ visible, onDismiss }: InventoryToastProps) {
  return (
    <AnimatePresence>
      {visible && (
        <motion.div
          className="fixed bottom-6 right-6 z-[100] px-5 py-4 rounded-xl border-2 bg-slate-900/95 border-cyan-400/60 shadow-[0_0_30px_rgba(34,211,238,0.35)] backdrop-blur-sm"
          initial={{ opacity: 0, x: 80, scale: 0.9 }}
          animate={{ opacity: 1, x: 0, scale: 1 }}
          exit={{ opacity: 0, x: 40 }}
          transition={{ type: "spring", damping: 25, stiffness: 300 }}
        >
          <div className="flex items-center gap-3">
            <span className="text-2xl" aria-hidden>
              📦
            </span>
            <div>
              <p
                className="font-bold text-white/95 tracking-wide"
                style={{ fontFamily: "Orbitron, sans-serif" }}
              >
                发现新战略物资，技能面板已更新！
              </p>
              <p className="text-sm text-cyan-300/80 mt-0.5">
                云边同步完成 · 新技能已就绪
              </p>
            </div>
            <button
              onClick={onDismiss}
              className="ml-2 p-1 rounded hover:bg-white/10 text-slate-400 hover:text-white transition-colors"
              aria-label="关闭"
            >
              ✕
            </button>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
