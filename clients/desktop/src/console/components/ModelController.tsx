/**
 * ModelController - 当前模型卡片 + 上下文窗口环形进度 + 模型热切换
 */

import { useState, useEffect, useCallback } from "react";
import { motion } from "framer-motion";
import { Loader2 } from "lucide-react";
import { getModels, setCurrentModel, getLlmContext, resetLlmContext } from "../../lib/api";
import type { ModelItem } from "../../lib/api";
import { cn } from "../../utils/cn";

const CONTEXT_MAX = 8192;
const CONTEXT_USED_DEFAULT = 4096;

export function ModelController({
  className,
  contextUsed = CONTEXT_USED_DEFAULT,
  contextMax = CONTEXT_MAX,
  modelName: modelNameProp = "Qwen-72B (Int4)",
  modelSub = "Quantized",
}: {
  className?: string;
  contextUsed?: number;
  contextMax?: number;
  modelName?: string;
  modelSub?: string;
}) {
  const [used, setUsed] = useState(contextUsed);
  const [ctxMax, setCtxMax] = useState(contextMax);
  const [models, setModels] = useState<ModelItem[]>([]);
  const [current, setCurrent] = useState<string>(modelNameProp);
  const [switching, setSwitching] = useState(false);

  useEffect(() => {
    setUsed(contextUsed);
  }, [contextUsed]);

  useEffect(() => {
    setCtxMax(contextMax);
  }, [contextMax]);

  const fetchLlmContext = useCallback(async () => {
    try {
      const ctx = await getLlmContext();
      if (ctx?.used != null) setUsed(ctx.used);
      if (ctx?.max != null) setCtxMax(ctx.max);
    } catch {
      // 保持默认
    }
  }, []);

  useEffect(() => {
    fetchLlmContext();
    const t = setInterval(fetchLlmContext, 8000);
    return () => clearInterval(t);
  }, [fetchLlmContext]);

  const fetchModels = useCallback(async () => {
    try {
      const res = await getModels();
      if (res?.models?.length) setModels(res.models);
      if (res?.current) setCurrent(res.current);
    } catch {
      setModels([]);
    }
  }, []);

  useEffect(() => {
    fetchModels();
    const t = setInterval(fetchModels, 30000);
    return () => clearInterval(t);
  }, [fetchModels]);

  useEffect(() => {
    if (modelNameProp) setCurrent(modelNameProp);
  }, [modelNameProp]);

  const handleSwitch = useCallback(async (modelId: string) => {
    if (modelId === current) return;
    setSwitching(true);
    try {
      await setCurrentModel(modelId);
      setCurrent(modelId);
    } catch (e) {
      console.error("switch model failed:", e);
    } finally {
      setSwitching(false);
    }
  }, [current]);

  const displayName = modelNameProp || current;
  const percent = Math.min(100, Math.round((used / ctxMax) * 100));
  const circumference = 2 * Math.PI * 42;
  const strokeDashoffset = circumference - (percent / 100) * circumference;

  return (
    <div className={cn("flex flex-col h-full min-h-0", className)}>
      <div className="flex-shrink-0 text-xs uppercase tracking-wider text-slate-500 mb-3 font-mono">
        Current Model
      </div>
      <motion.div
        layout
        className="flex-1 glass-panel rounded-xl p-5 flex flex-col justify-between min-h-0"
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        <div>
          <div className="font-mono text-lg font-semibold text-white">{displayName}</div>
          <div className="text-xs text-slate-400 mt-0.5">{modelSub}</div>
        </div>
        <div className="mt-4 pt-4 border-t border-white/10">
          <div className="text-[10px] uppercase tracking-wider text-slate-500 mb-2 font-mono">
            Context Window
          </div>
          <div className="flex items-center gap-4">
            <div className="relative w-28 h-28 flex-shrink-0">
              <svg className="w-full h-full -rotate-90" viewBox="0 0 96 96">
                <circle
                  cx="48"
                  cy="48"
                  r="42"
                  fill="none"
                  stroke="rgba(255,255,255,0.08)"
                  strokeWidth="8"
                />
                <motion.circle
                  cx="48"
                  cy="48"
                  r="42"
                  fill="none"
                  stroke="rgb(34, 211, 238)"
                  strokeWidth="8"
                  strokeLinecap="round"
                  strokeDasharray={circumference}
                  initial={{ strokeDashoffset: circumference }}
                  animate={{ strokeDashoffset }}
                  transition={{ duration: 0.6, ease: "easeOut" }}
                />
              </svg>
              <div className="absolute inset-0 flex items-center justify-center">
                <span className="font-mono text-lg text-cyan-400">{percent}%</span>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <span className="font-mono text-sm text-slate-400">
                <span className="text-cyan-400">{used.toLocaleString()}</span>
                <span className="text-slate-500"> / </span>
                <span>{ctxMax.toLocaleString()}</span>
                <span className="text-slate-500 ml-1">tokens</span>
              </span>
              <button
                type="button"
                onClick={async () => {
                  try {
                    await resetLlmContext();
                    setUsed(0);
                  } catch (e) {
                    console.error("reset failed:", e);
                  }
                }}
                className="text-[10px] text-slate-500 hover:text-cyan-400 font-mono"
                title="新会话"
              >
                新会话
              </button>
            </div>
          </div>
        </div>
        <div className="mt-4 pt-3 border-t border-white/10">
          {models.length > 0 ? (
            <div className="space-y-2">
              <div className="text-[10px] uppercase tracking-wider text-slate-500 mb-2 font-mono">
                切换模型
              </div>
              <div className="flex flex-wrap gap-2">
                {models.map((m) => (
                  <button
                    key={m.id}
                    type="button"
                    disabled={switching}
                    onClick={() => handleSwitch(m.id)}
                    className={cn(
                      "px-3 py-1.5 rounded-lg text-xs font-mono transition-colors",
                      m.id === current
                        ? "bg-cyan-500/30 text-cyan-400 border border-cyan-500/50"
                        : "bg-white/5 text-slate-400 border border-white/10 hover:bg-white/10 hover:text-slate-300"
                    )}
                  >
                    {m.name}
                  </button>
                ))}
              </div>
              {switching && (
                <p className="text-[10px] text-cyan-400/80 flex items-center gap-1">
                  <Loader2 className="w-3 h-3 animate-spin" /> 切换中…
                </p>
              )}
            </div>
          ) : (
            <button
              type="button"
              disabled
              title="模型列表加载中"
              className="w-full py-2 rounded-lg border border-white/10 bg-white/5 text-slate-500 text-xs font-mono cursor-not-allowed"
            >
              切换模型 (加载中…)
            </button>
          )}
        </div>
      </motion.div>
    </div>
  );
}
