/**
 * Horizon - 战术视野：极简悬浮状态条 + 等宽大写终端风
 * 环境、算力池、当前大脑模型
 */

import { useState, useEffect, useCallback } from "react";
import { motion } from "framer-motion";
import { invoke } from "@tauri-apps/api/core";
import { getClusterStats, getGpuStats } from "../../lib/api";
import { cn } from "../../utils/cn";
import { useDesktopUiLang } from "../../hooks/useDesktopUiLang";
import { desktopHorizon } from "../../utils/desktopUiI18n";
import { DesktopLanguageMenu } from "../../components/DesktopLanguageMenu";
import { ConsoleInboxCenter } from "./ConsoleInboxCenter";

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
  const [lang] = useDesktopUiLang();
  const hz = desktopHorizon[lang];
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
        "relative z-[100] flex-shrink-0 w-full flex items-center justify-between gap-3 px-4 pt-3 pb-2 pointer-events-none",
        className
      )}
    >
      <div className="pointer-events-auto flex min-w-0 items-center gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[8px] border border-cyan-200/[0.09] bg-cyan-300/[0.04] shadow-[inset_0_0_18px_rgba(56,189,248,0.025)]">
          <span className="font-semibold text-cyan-100">J</span>
        </div>
        <div className="min-w-0">
          <p className="truncate text-sm font-medium tracking-normal text-cyan-50/95">Jachin Control</p>
          <p className="truncate text-[11px] text-slate-500">{environment}</p>
        </div>
      </div>
      <motion.div
        layout
        className={cn(
          "pointer-events-auto flex flex-wrap items-center justify-end gap-x-3 gap-y-1 rounded-[8px] border border-cyan-200/[0.085] bg-slate-950/46 px-3 py-2 text-[11px] text-slate-400 backdrop-blur-xl",
          "shadow-[0_16px_48px_rgba(0,0,0,0.22),inset_0_0_22px_rgba(56,189,248,0.025)]",
          gpuOverheat && "border-red-500/40 shadow-[0_0_20px_rgba(239,68,68,0.25),inset_0_0_0_1px_rgba(248,113,113,0.2)]"
        )}
        {...(gpuOverheat
          ? {
              animate: {
                boxShadow: [
                  "0 0 16px rgba(239,68,68,0.2)",
                  "0 0 28px rgba(239,68,68,0.45)",
                  "0 0 16px rgba(239,68,68,0.2)",
                ],
              },
              transition: { duration: 1.2, repeat: Infinity as number, ease: "easeInOut" as const },
            }
          : {})}
      >
        {gpuOverheat && (
          <span
            className="text-red-400/95 animate-pulse drop-shadow-[0_0_10px_rgba(248,113,113,0.9)]"
            title={hz.gpuHotTitle}
          >
            {hz.gpuHot}
          </span>
        )}
        <span
          className={cn(
            "rounded-full border px-2.5 py-1 font-mono text-[10px] uppercase tracking-[0.12em]",
            clusterSummary
              ? "border-cyan-200/[0.1] bg-cyan-300/[0.04] text-cyan-100/90"
              : "border-cyan-200/[0.065] bg-cyan-300/[0.02] text-slate-500"
          )}
          title="算力池状态"
        >
          {clusterSummary ?? "Ray: —"}
        </span>
        <span className="max-w-[220px] truncate rounded-full border border-cyan-200/[0.065] bg-cyan-300/[0.02] px-2.5 py-1 font-mono text-[10px] uppercase tracking-[0.12em] text-slate-400" title="当前大脑模型">
          Brain: {modelName}
        </span>
        <div className="flex shrink-0 items-center gap-2 normal-case tracking-normal [&_button]:uppercase [&_button]:tracking-[0.12em]">
          <ConsoleInboxCenter lang={lang} />
          <DesktopLanguageMenu />
          <button
            type="button"
            className="rounded-[8px] border border-cyan-200/[0.065] bg-cyan-300/[0.02] px-3 py-1.5 text-[10px] font-mono uppercase tracking-[0.15em] text-slate-500 transition-all hover:border-rose-300/25 hover:bg-rose-300/[0.035] hover:text-rose-200"
            title={hz.exitTitle}
            onClick={() => void invoke("app_exit")}
          >
            {hz.exit}
          </button>
        </div>
      </motion.div>
    </header>
  );
}
