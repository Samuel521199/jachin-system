/**
 * SkillDetailModal - 技能详情弹层：描述、能力列表、逐项执行、上次结果
 */

import { Play, Loader2, X } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { SkillInfo } from "../../lib/api";
import { cn } from "../../utils/cn";

export function SkillDetailModal({
  skill,
  onClose,
  onExecute,
  executing,
  lastResult,
}: {
  skill: SkillInfo;
  onClose: () => void;
  onExecute: (skillId: string, capName: string) => void;
  executing: { skillId: string; cap: string } | null;
  lastResult?: string | null;
}) {
  const caps = skill.capabilities ?? [];
  const capNames = caps
    .map((c) => (c.name as string) || (typeof c === "string" ? c : ""))
    .filter(Boolean);

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
        onClick={onClose}
      >
        <motion.div
          initial={{ opacity: 0, scale: 0.96 }}
          animate={{ opacity: 1, scale: 1 }}
          exit={{ opacity: 0, scale: 0.96 }}
          transition={{ duration: 0.2 }}
          className={cn(
            "glass-panel rounded-xl border border-white/10 shadow-2xl max-w-lg w-full max-h-[80vh] flex flex-col overflow-hidden"
          )}
          onClick={(e) => e.stopPropagation()}
        >
          <div className="flex items-center justify-between p-4 border-b border-white/10 flex-shrink-0">
            <h3 className="font-mono font-semibold text-white">{skill.name}</h3>
            <button
              type="button"
              onClick={onClose}
              className="p-1.5 rounded-lg hover:bg-white/10 text-slate-400 hover:text-white transition-colors"
              aria-label="关闭"
            >
              <X className="w-5 h-5" />
            </button>
          </div>
          <div className="p-4 overflow-y-auto flex-1 min-h-0 custom-scrollbar">
            {skill.description && (
              <p className="text-sm text-slate-400 mb-4">{skill.description}</p>
            )}
            <div className="text-[10px] uppercase tracking-wider text-slate-500 mb-2 font-mono">
              能力
            </div>
            <ul className="space-y-2">
              {capNames.map((name) => {
                const isExec =
                  executing?.skillId === skill.skill_id && executing?.cap === name;
                return (
                  <li
                    key={`${skill.skill_id}-${name}`}
                    className="flex items-center justify-between gap-3 py-2 px-3 rounded-lg bg-white/5 border border-white/5"
                  >
                    <span className="font-mono text-sm text-slate-300">{name}</span>
                    <button
                      type="button"
                      onClick={() => onExecute(skill.skill_id, name)}
                      disabled={!!executing}
                      className="px-3 py-1.5 rounded text-xs font-mono bg-rose-600/80 hover:bg-rose-500 text-white disabled:opacity-50 flex items-center gap-1"
                    >
                      {isExec ? (
                        <Loader2 className="w-3 h-3 animate-spin" />
                      ) : (
                        <Play className="w-3 h-3" />
                      )}
                      执行
                    </button>
                  </li>
                );
              })}
            </ul>
            {lastResult != null && lastResult !== "" && (
              <div className="mt-4 p-3 rounded-lg bg-black/40 border border-rose-500/20">
                <div className="text-[10px] uppercase tracking-wider text-slate-500 mb-1 font-mono">
                  上次执行结果
                </div>
                <pre className="text-xs overflow-x-auto max-h-32 overflow-y-auto whitespace-pre-wrap font-mono custom-scrollbar">
                  {lastResult}
                </pre>
              </div>
            )}
          </div>
        </motion.div>
      </motion.div>
    </AnimatePresence>
  );
}
