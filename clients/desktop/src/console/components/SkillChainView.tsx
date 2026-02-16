/**
 * Skill Chain View - 技能组合视图：展示最近一次 AI 组合多技能完成任务的链条
 * 设计愿景 5.3：过程可见。当前为占位 + 自然语言执行后的简化链，后端可扩展返回 chain 数组
 */

import { motion } from "framer-motion";
import { ChevronRight } from "lucide-react";
import { cn } from "../../utils/cn";

export interface ChainStep {
  id: string;
  label: string;
  type?: "input" | "intent" | "skill" | "done";
}

export function SkillChainView({
  steps,
  className,
}: {
  /** 链条步骤；空则显示占位。可由自然语言执行结果或后端 metadata.chain 填充 */
  steps: ChainStep[];
  className?: string;
}) {
  const hasSteps = steps.length > 0;

  return (
    <div
      className={cn(
        "glass-panel rounded-xl p-4 flex flex-col min-h-0",
        className
      )}
    >
      <h2 className="font-mono text-xs uppercase tracking-wider text-slate-500 mb-3 flex-shrink-0">
        最近执行链 (Chain View)
      </h2>
      <div className="flex-1 min-h-0 overflow-x-auto overflow-y-hidden py-2 custom-scrollbar">
        {!hasSteps ? (
          <p className="text-slate-500 text-sm font-mono py-2">
            暂无最近执行链。使用上方「自然语言执行」后将在此展示。
          </p>
        ) : (
          <motion.div
            className="flex items-center gap-1 flex-wrap"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.25 }}
          >
            {steps.map((step, i) => (
              <span key={step.id} className="flex items-center gap-1 flex-shrink-0">
                <span
                  className={cn(
                    "px-2.5 py-1 rounded font-mono text-xs border",
                    step.type === "input" && "bg-cyan-500/10 border-cyan-500/30 text-cyan-300/90",
                    step.type === "intent" && "bg-violet-500/10 border-violet-500/30 text-violet-300/90",
                    step.type === "skill" && "bg-rose-500/10 border-rose-500/30 text-rose-300/90",
                    step.type === "done" && "bg-emerald-500/10 border-emerald-500/30 text-emerald-300/90",
                    !step.type && "bg-white/5 border-white/10 text-slate-300"
                  )}
                >
                  {step.label}
                </span>
                {i < steps.length - 1 && (
                  <ChevronRight className="w-4 h-4 text-slate-500 flex-shrink-0" aria-hidden />
                )}
              </span>
            ))}
          </motion.div>
        )}
      </div>
    </div>
  );
}
