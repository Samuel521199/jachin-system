/**
 * SystemHeartbeat - 系统心跳：频谱式竖条刻度（负载越高点亮越多，青→红）
 * 随 CPU/GPU 负载跳动，置于侧栏底部
 */

import { useState, useEffect, useCallback, useMemo, type CSSProperties } from "react";
import { motion } from "framer-motion";
import { invoke } from "@tauri-apps/api/core";
import { getGpuStats } from "../../lib/api";
import { cn } from "../../utils/cn";
import { useDesktopUiLang } from "../../hooks/useDesktopUiLang";
import { getDesktopConsole } from "../../utils/desktopUiI18n";

const FALLBACK_CPU = 24;
const GPU_OVERHEAT_THRESHOLD = 85;
const TICK_COUNT = 40;

type TickModel =
  | { on: false; className: string }
  | { on: true; overheated: true; className: string }
  | { on: true; overheated: false; style: CSSProperties };

export function SystemHeartbeat() {
  const [lang] = useDesktopUiLang();
  const c = getDesktopConsole(lang);
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
  const litCount = Math.round((clamped / 100) * TICK_COUNT);
  const isOverheated = gpuTemp != null && gpuTemp >= GPU_OVERHEAT_THRESHOLD;

  const ticks = useMemo(() => {
    return Array.from({ length: TICK_COUNT }, (_, i): TickModel => {
      const on = i < litCount;
      if (!on) {
        return { on: false, className: "bg-slate-800/90 h-2 opacity-40" };
      }
      if (isOverheated) {
        return {
          on: true,
          overheated: true,
          className:
            "h-3 sm:h-4 bg-gradient-to-t from-rose-700 to-amber-400 opacity-95 shadow-[0_0_8px_rgba(251,113,133,0.7)]",
        };
      }
      const t = TICK_COUNT <= 1 ? 0 : i / (TICK_COUNT - 1);
      const r = Math.round(34 + t * 200);
      const g = Math.round(211 - t * 160);
      const b = Math.round(238 - t * 120);
      const height = `${10 + (i / TICK_COUNT) * 10}px`;
      return {
        on: true,
        overheated: false,
        style: {
          height,
          backgroundColor: `rgb(${r} ${g} ${b})`,
          boxShadow: `0 0 6px rgba(${r},${g},${b},0.45)`,
        },
      };
    });
  }, [litCount, isOverheated]);

  return (
    <div className="jarvis-heartbeat relative z-10 border-t border-cyan-200/[0.06] px-3 py-4">
      <div className="mb-3 flex items-center gap-2">
        <span className="relative flex h-7 w-7 shrink-0 items-center justify-center rounded-[7px] border border-cyan-200/[0.08] bg-cyan-300/[0.025]">
          <span className={cn("h-2 w-2 rounded-full", isOverheated ? "bg-amber-300 shadow-[0_0_12px_rgba(251,191,36,0.8)]" : "bg-cyan-200 shadow-[0_0_12px_rgba(125,211,252,0.65)]")} />
        </span>
        <div className="min-w-0 flex-1 opacity-0 transition-opacity duration-300 group-hover:opacity-100">
          <p className="truncate text-[10px] font-semibold uppercase tracking-[0.2em] text-cyan-100/58">
            System Heartbeat
          </p>
          <p className="mt-0.5 truncate text-[10px] font-mono tabular-nums text-slate-500">
            Tier 2 · {clamped}%
          </p>
        </div>
      </div>
      <div className="flex h-8 items-end justify-center gap-[3px] px-0.5">
        {ticks.map((tick, i) => {
          if (!tick.on) {
            return (
              <div
                key={i}
                className={cn("w-[2px] max-w-[3px] flex-1 rounded-full min-h-[5px]", tick.className)}
              />
            );
          }
          if (tick.overheated) {
            return (
              <motion.div
                key={i}
                className={cn("w-[2px] max-w-[3px] flex-1 rounded-full min-h-[5px]", tick.className)}
                animate={{ opacity: [0.75, 1, 0.75] }}
                transition={{ duration: 0.9, repeat: Infinity, ease: "easeInOut", delay: (i % 6) * 0.04 }}
              />
            );
          }
          return (
            <motion.div
              key={i}
              className="w-[2px] max-w-[3px] flex-1 rounded-full min-h-[5px]"
              style={tick.style}
              animate={{ opacity: [0.52, 1, 0.52] }}
              transition={{ duration: 1.2 + (i % 5) * 0.06, repeat: Infinity, ease: "easeInOut" }}
            />
          );
        })}
      </div>
      <p className="mt-2 hidden text-[10px] font-mono tabular-nums tracking-tight text-slate-500 group-hover:block">
        CPU {cpu}%
        {gpu != null ? ` · GPU ${gpu}%` : ""}
        {gpuTemp != null ? ` · ${gpuTemp}°C` : ""}
      </p>
      {isOverheated && gpuTemp != null && (
        <p className="text-[10px] text-amber-400 mt-1 font-mono animate-pulse" title={c.heartbeat.gpuHotTitle}>
          {c.heartbeat.gpuHot.replace("{temp}", String(gpuTemp))}
        </p>
      )}
    </div>
  );
}
