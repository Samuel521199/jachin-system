/**
 * SystemHeartbeat - 系统心跳脉冲条
 * 随 CPU/GPU 负载跳动，置于侧栏底部
 */

import { useState, useEffect, useCallback } from "react";
import { motion } from "framer-motion";
import { invoke } from "@tauri-apps/api/core";
import { getGpuStats } from "../../lib/api";
import { cn } from "../../utils/cn";

const FALLBACK_CPU = 24;
const GPU_OVERHEAT_THRESHOLD = 85;

export function SystemHeartbeat() {
  const [cpu, setCpu] = useState(FALLBACK_CPU);
  const [gpu, setGpu] = useState<number | null>(null);
  const [gpuTemp, setGpuTemp] = useState<number | null>(null);

  const fetchCpu = useCallback(async () => {
    try {
      const raw = await invoke<{ cpu_usage_percent: number }>("get_system_stats");
      setCpu(Math.round(raw.cpu_usage_percent));
    } catch {
      setCpu(FALLBACK_CPU);
    }
  }, []);

  const fetchGpu = useCallback(async () => {
    try {
      const res = await getGpuStats();
      const g0 = res?.gpus?.[0];
      setGpu(g0?.utilization_gpu ?? null);
      setGpuTemp(g0?.temperature_c ?? null);
    } catch {
      setGpu(null);
      setGpuTemp(null);
    }
  }, []);

  useEffect(() => {
    fetchCpu();
    const t = setInterval(fetchCpu, 2000);
    return () => clearInterval(t);
  }, [fetchCpu]);

  useEffect(() => {
    fetchGpu();
    const t = setInterval(fetchGpu, 4000);
    return () => clearInterval(t);
  }, [fetchGpu]);

  const load = gpu != null ? Math.max(cpu, gpu) : cpu;
  const clamped = Math.min(100, Math.max(0, load));
  const widthPercent = 20 + (clamped / 100) * 80;
  const isOverheated = gpuTemp != null && gpuTemp >= GPU_OVERHEAT_THRESHOLD;

  return (
    <div className="px-3 py-4 border-t border-white/10">
      <p className="text-[10px] uppercase tracking-widest text-slate-500 mb-2 font-mono">
        System Heartbeat
      </p>
      <div className="h-1.5 rounded-full bg-black/40 overflow-hidden border border-white/5">
        <motion.div
          className={cn(
            "h-full rounded-full",
            isOverheated ? "bg-amber-500/90" : "bg-gradient-to-r from-rose-500/80 to-cyan-500/80"
          )}
          initial={{ width: "20%" }}
          animate={{ width: `${widthPercent}%` }}
          transition={{ type: "spring", stiffness: 120, damping: 20 }}
          style={{
            boxShadow: isOverheated ? "0 0 16px rgba(245, 158, 11, 0.6)" : "0 0 12px rgba(244, 63, 94, 0.4)",
          }}
        />
      </div>
      <p className="text-[10px] text-slate-500 mt-1.5 font-mono tabular-nums">
        Tier 2 · {clamped}%
        {gpu != null ? ` (CPU ${cpu} · GPU ${gpu})` : ""}
        {gpuTemp != null ? ` · ${gpuTemp}°C` : ""}
      </p>
      {isOverheated && (
        <p className="text-[10px] text-amber-400 mt-1 font-mono" title="算力负载过高，建议分流任务到云端">
          ⚠ GPU 过热 ({gpuTemp}°C)
        </p>
      )}
    </div>
  );
}
