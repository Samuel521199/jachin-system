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
        "relative z-[100] flex-shrink-0 w-full flex justify-end px-4 pt-2 pb-1 pointer-events-none",
        className
      )}
    >
      <motion.div
        layout
        className={cn(
          "pointer-events-auto flex flex-wrap items-center justify-end gap-x-4 gap-y-1 border border-cyan-500/25 bg-black/50 px-4 py-2 font-mono text-[10px] uppercase tracking-[0.18em] text-slate-400 backdrop-blur-md",
          "shadow-[0_0_24px_rgba(0,0,0,0.65),inset_0_0_0_1px_rgba(6,182,212,0.12)]",
          "[clip-path:polygon(0_0,calc(100%-14px)_0,100%_14px,100%_100%,14px_100%,0_calc(100%-14px))]",
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
        <span className="text-slate-500 max-w-[200px] truncate" title="当前环境">
          {environment}
        </span>
        <span className="text-cyan-800/80">│</span>
        <span
          className={clusterSummary ? "text-cyan-300/95 drop-shadow-[0_0_6px_rgba(34,211,238,0.35)]" : "text-slate-600"}
          title="算力池状态"
        >
          {clusterSummary ?? "Ray: —"}
        </span>
        <span className="text-cyan-800/80">│</span>
        <span className="text-slate-500 max-w-[220px] truncate" title="当前大脑模型">
          Brain: {modelName}
        </span>
        <span className="text-cyan-800/60">│</span>
        <div className="flex shrink-0 items-center gap-2 normal-case tracking-normal [&_button]:uppercase [&_button]:tracking-[0.12em]">
          <ConsoleInboxCenter lang={lang} />
          <DesktopLanguageMenu />
          <button
            type="button"
            className="border border-white/10 bg-black/30 px-3 py-1 text-[10px] font-mono uppercase tracking-[0.15em] text-slate-500 transition-all hover:border-rose-400/50 hover:text-rose-300/95 hover:shadow-[inset_0_0_12px_rgba(244,63,94,0.15)] active:shadow-[inset_0_0_16px_rgba(6,182,212,0.25)]"
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
