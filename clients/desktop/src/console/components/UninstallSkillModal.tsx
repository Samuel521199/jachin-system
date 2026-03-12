/**
 * 卸载技能 Modal - 危险警告 + purge_data 复选框
 */

import React, { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { AlertTriangle, Trash2, Loader2 } from "lucide-react";

export interface UninstallSkillModalProps {
  skillName: string;
  itemId: string;
  onConfirm: (purgeData: boolean) => Promise<void>;
  onClose: () => void;
}

export function UninstallSkillModal({
  skillName,
  itemId,
  onConfirm,
  onClose,
}: UninstallSkillModalProps) {
  const [purgeData, setPurgeData] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleConfirm = async () => {
    setError(null);
    setLoading(true);
    try {
      await onConfirm(purgeData);
      onClose();
    } catch (e) {
      const msg = e instanceof Error ? e.message : (typeof e === "object" && e && "message" in e ? String((e as { message: unknown }).message) : String(e));
      setError(msg || "卸载失败，请重试");
      console.error("[UninstallSkillModal] 卸载失败:", e);
    } finally {
      setLoading(false);
    }
  };

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
        onClick={onClose}
      >
        <motion.div
          initial={{ scale: 0.95, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          exit={{ scale: 0.95, opacity: 0 }}
          transition={{ type: "spring", damping: 25, stiffness: 300 }}
          className="glass-panel rounded-2xl p-6 max-w-md w-full mx-4 border border-rose-500/30 shadow-2xl"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="flex items-center gap-3 mb-4">
            <div className="p-2 rounded-lg bg-rose-500/20">
              <AlertTriangle className="w-6 h-6 text-rose-400" />
            </div>
            <div>
              <h3 className="font-mono font-semibold text-white">确定要卸载该技能吗？</h3>
              <p className="text-sm text-slate-400 mt-0.5">{skillName}</p>
            </div>
          </div>

          <p className="text-slate-300 text-sm mb-4">
            将移入回收站，可从回收站恢复或彻底删除。若勾选下方选项，将同时清理该技能产生的配置与数据卷。
          </p>

          {error && (
            <p className="text-amber-400 text-sm mb-4 p-3 rounded-lg bg-amber-500/10 border border-amber-500/30">
              {error}
            </p>
          )}

          <label className="flex items-start gap-3 cursor-pointer mb-6 p-3 rounded-lg bg-white/5 border border-white/10 hover:border-rose-500/30 transition-colors">
            <input
              type="checkbox"
              checked={purgeData}
              onChange={(e) => setPurgeData(e.target.checked)}
              className="mt-1 rounded border-slate-500 bg-slate-800 text-rose-500 focus:ring-rose-500"
            />
            <span className="text-sm text-slate-300">
              <span className="text-amber-400 font-medium">⚠️ 同时删除</span>该技能产生的配置文件和本地数据卷
              （若有其他技能共享则保留）
            </span>
          </label>

          <div className="flex gap-3 justify-end">
            <button
              type="button"
              onClick={onClose}
              disabled={loading}
              className="px-4 py-2 rounded-lg bg-slate-700/80 hover:bg-slate-600 text-slate-300 disabled:opacity-50 font-mono text-sm"
            >
              取消
            </button>
            <button
              type="button"
              onClick={handleConfirm}
              disabled={loading}
              className="flex items-center gap-2 px-4 py-2 rounded-lg bg-rose-600 hover:bg-rose-500 text-white disabled:opacity-50 font-mono text-sm"
            >
              {loading ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Trash2 className="w-4 h-4" />
              )}
              移入回收站
            </button>
          </div>
        </motion.div>
      </motion.div>
    </AnimatePresence>
  );
}
