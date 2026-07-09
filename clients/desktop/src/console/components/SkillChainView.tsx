import { motion } from "framer-motion";
import { ChevronRight, CircleDot, Route } from "lucide-react";
import { cn } from "../../utils/cn";

export interface ChainStep {
  id: string;
  label: string;
  type?: "input" | "intent" | "skill" | "done";
}

const toneByType: Record<NonNullable<ChainStep["type"]>, string> = {
  input: "border-cyan-200/[0.18] bg-cyan-300/[0.05] text-cyan-50",
  intent: "border-violet-200/[0.17] bg-violet-300/[0.045] text-violet-100",
  skill: "border-rose-200/[0.18] bg-rose-300/[0.055] text-rose-100",
  done: "border-emerald-200/[0.18] bg-emerald-300/[0.05] text-emerald-100",
};

export function SkillChainView({
  steps,
  className,
}: {
  steps: ChainStep[];
  className?: string;
}) {
  const hasSteps = steps.length > 0;

  return (
    <div className={cn("jarvis-panel relative flex min-h-0 flex-col overflow-hidden rounded-[8px] border border-cyan-200/[0.08] bg-cyan-300/[0.018] p-4", className)}>
      <div className="relative z-10 mb-3 flex flex-shrink-0 items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className="flex h-8 w-8 items-center justify-center rounded-[7px] border border-cyan-200/[0.09] bg-cyan-300/[0.035] text-cyan-100/90">
            <Route className="h-4 w-4" />
          </span>
          <div>
            <h2 className="font-mono text-[11px] uppercase tracking-[0.16em] text-cyan-100/80">Chain View</h2>
            <p className="mt-0.5 text-xs text-slate-500">最近一次 AI 组合执行路径</p>
          </div>
        </div>
        <span className="rounded-full border border-cyan-200/[0.08] bg-cyan-300/[0.025] px-2.5 py-1 font-mono text-[10px] uppercase tracking-[0.12em] text-slate-500">
          {steps.length || 0} nodes
        </span>
      </div>

      <div className="relative z-10 min-h-0 flex-1 overflow-x-auto overflow-y-hidden py-1 custom-scrollbar">
        {!hasSteps ? (
          <div className="rounded-[8px] border border-cyan-200/[0.055] bg-slate-950/28 px-4 py-4 text-sm text-slate-500">
            暂无最近执行链。使用上方命令模块后将在这里显示。
          </div>
        ) : (
          <motion.div
            className="flex min-w-max items-center gap-2"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.25 }}
          >
            {steps.map((step, i) => (
              <span key={step.id} className="flex flex-shrink-0 items-center gap-2">
                <span
                  className={cn(
                    "inline-flex items-center gap-2 rounded-[8px] border px-3 py-2 font-mono text-xs shadow-[inset_0_0_18px_rgba(56,189,248,0.025)]",
                    step.type ? toneByType[step.type] : "border-cyan-200/[0.08] bg-cyan-300/[0.025] text-slate-300"
                  )}
                >
                  <CircleDot className="h-3.5 w-3.5" />
                  {step.label}
                </span>
                {i < steps.length - 1 && <ChevronRight className="h-4 w-4 text-cyan-100/28" aria-hidden />}
              </span>
            ))}
          </motion.div>
        )}
      </div>
    </div>
  );
}
