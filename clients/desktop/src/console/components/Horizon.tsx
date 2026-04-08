/**
 * Horizon - 顶部状态栏 (The Horizon)
 * 设计愿景 2.3：环境、算力池、当前大脑模型
 */

import { useState, useEffect, useCallback } from "react";
import { invoke } from "@tauri-apps/api/core";
import { getClusterStats, getGpuStats } from "../../lib/api";
import { cn } from "../../utils/cn";

const GPU_OVERHEAT_THRESHOLD = 85;

export function Horizon({
  className,
  environment = "Home Network (Secure)",
  modelName = "Qwen-72B (Int4)",
}: {
  className?: string;
  environment?: string;
  modelName?: string;
}) {
  const [clusterSummary, setClusterSummary] = useState<string | null>(null);
  const [gpuOverheat, setGpuOverheat] = useState(false);

  const fetchGpuOverheat = useCallback(async () => {
    try {
      const res = await getGpuStats();
      const temp = res?.gpus?.[0]?.temperature_c ?? null;
      setGpuOverheat(temp != null && temp >= GPU_OVERHEAT_THRESHOLD);
    } catch {
      setGpuOverheat(false);
    }
  }, []);

  useEffect(() => {
    fetchGpuOverheat();
    const t = setInterval(fetchGpuOverheat, 5000);
    return () => clearInterval(t);
  }, [fetchGpuOverheat]);

  const fetchCluster = useCallback(async () => {
    try {
      const stats = await getClusterStats();
      const nodes = stats?.nodes ?? {};
      const online = nodes.online ?? 0;
      const total = nodes.total ?? 0;
      setClusterSummary(total > 0 ? `Ray: ${online}/${total} nodes` : null);
    } catch {
      setClusterSummary(null);
    }
  }, []);

  useEffect(() => {
    fetchCluster();
    const t = setInterval(fetchCluster, 12000);
    return () => clearInterval(t);
  }, [fetchCluster]);

  return (
    <header
      className={cn(
        "flex-shrink-0 h-9 px-4 flex items-center gap-6 border-b border-white/10 bg-black/20 backdrop-blur-sm",
        "font-mono text-xs text-slate-400",
        gpuOverheat && "border-amber-500/40",
        className
      )}
    >
      {gpuOverheat && (
        <span className="text-amber-400" title="GPU 过热，建议分流任务到云端">
          ⚠ 算力过热
        </span>
      )}
      <span className="text-slate-500" title="当前环境">
        {environment}
      </span>
      <span className="text-slate-600">|</span>
      <span
        className={clusterSummary ? "text-cyan-400/90" : "text-slate-500"}
        title="算力池状态"
      >
        {clusterSummary ?? "Ray: —"}
      </span>
      <span className="text-slate-600">|</span>
      <span className="text-slate-500" title="当前大脑模型">
        Brain: {modelName}
      </span>
      <button
        type="button"
        className="ml-auto shrink-0 rounded px-2 py-0.5 text-slate-500 transition-colors hover:bg-white/5 hover:text-rose-300/90"
        title="完全退出 Jachin（结束进程；关闭主窗口仅会隐藏，请用此处或托盘菜单退出）"
        onClick={() => void invoke("app_exit")}
      >
        退出
      </button>
    </header>
  );
}
