/**
 * Dashboard - 战情室 (Situation Room)
 * MindStream | 中区固定约 3/5 原高度带（ComputeTopology + Quick Actions）| Agenda
 */

import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { motion } from "framer-motion";
import { invoke } from "@tauri-apps/api/core";
import { MindStream } from "../components/MindStream";
import { ComputeTopology } from "../components/ComputeTopology";
import { getClusterStats, getSuggestions, getLogsRecent, executeSuggestion, getGpuStats, getClusterNodes, getClusterTasks } from "../../lib/api";
import type { SuggestionItem, GpuStatsItem, ClusterNodeInfo, ClusterTaskInfo } from "../../lib/api";
import { cn } from "../../utils/cn";
import { useDesktopUiLang } from "../../hooks/useDesktopUiLang";
import { getDesktopConsole, localizeMindStreamLine } from "../../utils/desktopUiI18n";
import { readDesktopUiLang } from "../../utils/desktopUiLang";

export type { SuggestionItem };

interface SystemStats {
  cpu_usage_percent: number;
  memory_total_bytes: number;
  memory_used_bytes: number;
}

export function Dashboard({
  suggestions: suggestionsProp,
  onSuggestionAction,
}: {
  /** 建议卡片数据，不传则使用占位数据；后端可传入主动推送的建议 */
  suggestions?: SuggestionItem[];
  /** 点击建议卡片按钮时回调，便于后端执行或记录 */
  onSuggestionAction?: (suggestionId: string, action: string) => void;
} = {}) {
  const [lang] = useDesktopUiLang();
  const c = useMemo(() => getDesktopConsole(lang), [lang]);
  const quickActions = useMemo(
    () =>
      [
        {
          id: "privacy",
          label: c.dashboard.quickPrivacy,
          icon: "🛡️",
          cmd: "quick_action_privacy_mode",
          isToggle: true as const,
          title: c.dashboard.quickPrivacyTitle,
        },
        {
          id: "clean",
          label: c.dashboard.quickClean,
          icon: "🧹",
          cmd: "quick_action_clear_memory",
          isToggle: false as const,
          title: c.dashboard.quickCleanTitle,
        },
        {
          id: "eagle",
          label: c.dashboard.quickEagle,
          icon: "👁️",
          cmd: "quick_action_eagle_eye",
          isToggle: true as const,
          title: c.dashboard.quickEagleTitle,
        },
        {
          id: "sleep",
          label: c.dashboard.quickSleep,
          icon: "💤",
          cmd: "quick_action_hibernate",
          isToggle: true as const,
          title: c.dashboard.quickSleepTitle,
        },
      ] as const,
    [c],
  );

  const suggestionsFromApiRef = useRef(false);
  const [stats, setStats] = useState<SystemStats | null>(null);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [privacyMode, setPrivacyMode] = useState<boolean | null>(null);
  const [eagleEyeOn, setEagleEyeOn] = useState<boolean | null>(null);
  const [hibernateOn, setHibernateOn] = useState<boolean | null>(null);
  const [liveStatsLines, setLiveStatsLines] = useState<string[]>([]);
  const [liveLogLines, setLiveLogLines] = useState<string[]>([]);
  const [suggestions, setSuggestions] = useState<SuggestionItem[]>(() => [
    ...(getDesktopConsole(readDesktopUiLang()).demoSuggestions as unknown as SuggestionItem[]),
  ]);
  const [clusterStats, setClusterStats] = useState<{ nodes?: { total?: number }; tasks?: { running?: number } } | null>(null);
  const [gpuStats, setGpuStats] = useState<{ gpus: GpuStatsItem[] } | null>(null);
  const [clusterNodes, setClusterNodes] = useState<ClusterNodeInfo[]>([]);
  const [clusterTasks, setClusterTasks] = useState<ClusterTaskInfo[]>([]);
  const [isVoiceCaptureRunning, setIsVoiceCaptureRunning] = useState(false);

  const fetchClusterStats = useCallback(async () => {
    try {
      const cluster = await getClusterStats();
      setClusterStats(cluster);
      const nodes = cluster?.nodes ?? {};
      const tasks = cluster?.tasks ?? {};
      const online = nodes.online ?? 0;
      const total = nodes.total ?? 0;
      const running = tasks.running ?? 0;
      const totalTasks = tasks.total ?? 0;
      setLiveStatsLines([
        `Ray cluster: ${online}/${total} nodes, ${running} running task${running !== 1 ? "s" : ""}.`,
        totalTasks > 0 ? `Tasks: ${running} running, ${tasks.pending ?? 0} pending.` : "",
      ].filter(Boolean));
    } catch {
      setClusterStats(null);
      setLiveStatsLines([]);
    }
  }, []);

  useEffect(() => {
    fetchClusterStats();
    const t = setInterval(fetchClusterStats, 10000);
    return () => clearInterval(t);
  }, [fetchClusterStats]);

  const fetchGpuStats = useCallback(async () => {
    try {
      const res = await getGpuStats();
      if (res?.gpus?.length) setGpuStats({ gpus: res.gpus });
      else setGpuStats(null);
    } catch {
      setGpuStats(null);
    }
  }, []);

  useEffect(() => {
    fetchGpuStats();
    const t = setInterval(fetchGpuStats, 5000);
    return () => clearInterval(t);
  }, [fetchGpuStats]);

  const fetchClusterDetails = useCallback(async () => {
    try {
      const [nodes, tasks] = await Promise.all([
        getClusterNodes(),
        getClusterTasks({ limit: 10 }),
      ]);
      setClusterNodes(Array.isArray(nodes) ? nodes : []);
      setClusterTasks(Array.isArray(tasks) ? tasks : []);
    } catch {
      setClusterNodes([]);
      setClusterTasks([]);
    }
  }, []);

  useEffect(() => {
    fetchClusterDetails();
    const t = setInterval(fetchClusterDetails, 15000);
    return () => clearInterval(t);
  }, [fetchClusterDetails]);

  const fetchSuggestions = useCallback(async () => {
    try {
      const res = await getSuggestions();
      if (res?.items?.length) {
        suggestionsFromApiRef.current = true;
        setSuggestions(res.items);
      } else {
        suggestionsFromApiRef.current = false;
        setSuggestions([...(getDesktopConsole(lang).demoSuggestions as unknown as SuggestionItem[])]);
      }
    } catch {
      suggestionsFromApiRef.current = false;
      setSuggestions([...(getDesktopConsole(lang).demoSuggestions as unknown as SuggestionItem[])]);
    }
  }, [lang]);

  useEffect(() => {
    if (suggestionsFromApiRef.current) return;
    setSuggestions([...(getDesktopConsole(lang).demoSuggestions as unknown as SuggestionItem[])]);
  }, [lang]);

  const fetchLogs = useCallback(async () => {
    try {
      const res = await getLogsRecent(20);
      if (res?.lines?.length) setLiveLogLines(res.lines);
    } catch {
      setLiveLogLines([]);
    }
  }, []);

  useEffect(() => {
    fetchSuggestions();
    const t = setInterval(fetchSuggestions, 30000);
    return () => clearInterval(t);
  }, [fetchSuggestions]);

  useEffect(() => {
    fetchLogs();
    const t = setInterval(fetchLogs, 5000);
    return () => clearInterval(t);
  }, [fetchLogs]);

  const handleSuggestionAction = useCallback(
    async (suggestionId: string, action: string) => {
      onSuggestionAction?.(suggestionId, action);
      try {
        await executeSuggestion(suggestionId, action);
      } catch (e) {
        console.error("executeSuggestion failed:", e);
      }
    },
    [onSuggestionAction]
  );

  const displaySuggestions = suggestionsProp ?? suggestions;

  const fetchStats = useCallback(async () => {
    try {
      const raw = await invoke<SystemStats>("get_system_stats");
      setStats(raw);
    } catch {
      setStats(null);
    }
  }, []);

  useEffect(() => {
    fetchStats();
    const t = setInterval(fetchStats, 2000);
    return () => clearInterval(t);
  }, [fetchStats]);

  useEffect(() => {
    invoke<boolean>("get_privacy_mode").then(setPrivacyMode).catch(() => setPrivacyMode(false));
    invoke<boolean>("get_eagle_eye_mode").then(setEagleEyeOn).catch(() => setEagleEyeOn(false));
    invoke<boolean>("get_hibernate_mode").then(setHibernateOn).catch(() => setHibernateOn(false));
    invoke<boolean>("is_voice_capture_running").then(setIsVoiceCaptureRunning).catch(() => setIsVoiceCaptureRunning(false));
  }, []);

  const cpuPercent = stats ? Math.round(stats.cpu_usage_percent) : 0;
  const ramPercent = stats && stats.memory_total_bytes > 0
    ? Math.round((stats.memory_used_bytes / stats.memory_total_bytes) * 100)
    : 0;

  const runQuickAction = async (cmd: string, isToggle?: boolean) => {
    setActionLoading(cmd);
    try {
      if (isToggle && (cmd === "quick_action_privacy_mode" || cmd === "quick_action_eagle_eye" || cmd === "quick_action_hibernate")) {
        const next = await invoke<boolean>(cmd);
        if (cmd === "quick_action_privacy_mode") setPrivacyMode(next);
        if (cmd === "quick_action_eagle_eye") setEagleEyeOn(next);
        if (cmd === "quick_action_hibernate") setHibernateOn(next);
      } else {
        await invoke(cmd);
      }
    } catch (e) {
      console.error(e);
    } finally {
      setActionLoading(null);
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 p-5 sm:p-6">
      {/* Mind Stream：视口比例 + 下限，为 Agenda 留出纵向空间 */}
      <section className="h-[min(32vh,320px)] min-h-[180px] shrink-0">
        <MindStream
          className="h-full min-h-0"
          maxLines={6}
          demoLoop
          liveStatsLines={liveStatsLines}
          liveLogLines={liveLogLines}
          mindLocale={c.mind}
          localizeLine={(line) => localizeMindStreamLine(line, lang)}
        />
      </section>

      {/* 中区高度 ≈ 原 min(42vh,360px) 的 3/5；内部紧凑 + 溢出滚动，避免大块留白与堆叠 */}
      <section className="flex h-[min(25.2vh,216px)] shrink-0 items-stretch gap-4 md:gap-5">
        <motion.div
          className="dashboard-holo-fiber console-fiber-host console-holo-slab flex h-full min-h-0 min-w-0 flex-1 flex-col p-3 sm:p-4"
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.35 }}
        >
          <div className="flex min-h-0 w-full flex-1 flex-col items-center overflow-y-auto overflow-x-hidden [-webkit-overflow-scrolling:touch]">
            <div className="flex w-full max-w-md flex-shrink-0 flex-col items-center gap-1.5 pb-1">
              <ComputeTopology
                compact
                workerCount={Math.max(0, (clusterStats?.nodes?.total ?? 1) - 1)}
                activeWorkerIndex={(clusterStats?.tasks?.running ?? 0) > 0 ? 0 : (cpuPercent > 25 ? 0 : -1)}
                cpuPercent={cpuPercent}
                ramPercent={ramPercent}
                gpuStats={gpuStats?.gpus ?? undefined}
                nodes={clusterNodes}
                tasks={clusterTasks}
              />
            </div>
          </div>
        </motion.div>
        <motion.div
          className="dashboard-holo-fiber console-fiber-host console-holo-slab flex h-full min-h-0 w-[min(19rem,100%)] max-w-[19rem] flex-shrink-0 flex-col p-3 sm:p-4"
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.35, delay: 0.05 }}
        >
          <h2
            className="mb-1.5 shrink-0 font-mono text-[10px] font-semibold uppercase tracking-[0.28em] text-cyan-600/90"
            style={{ fontFamily: "Orbitron, sans-serif" }}
          >
            {c.dashboard.quickActionsTitle}
          </h2>
          <div className="flex min-h-0 flex-1 flex-col gap-2.5 overflow-y-auto overflow-x-hidden pr-0.5">
            <div className="isolate grid w-full max-w-[17.5rem] shrink-0 grid-cols-2 auto-rows-min content-start justify-items-center gap-x-2 gap-y-2 self-center py-0.5">
              {quickActions.map((action) => {
                const isOn =
                  (action.id === "privacy" && privacyMode) ||
                  (action.id === "eagle" && eagleEyeOn) ||
                  (action.id === "sleep" && hibernateOn);
                return (
                  <motion.button
                    key={action.id}
                    type="button"
                    disabled={actionLoading !== null}
                    onClick={() => runQuickAction(action.cmd, action.isToggle)}
                    title={action.title}
                    className={cn(
                      "console-hex-btn flex h-[72px] w-[68px] max-h-[72px] max-w-[68px] shrink-0 flex-col items-center justify-center gap-0 border text-[8px] font-semibold uppercase leading-tight tracking-wide transition-all disabled:opacity-60",
                      "border-cyan-500/30 bg-black/50 shadow-[inset_0_0_0_1px_rgba(6,182,212,0.08)]",
                      isOn
                        ? "border-emerald-400/60 text-emerald-300 shadow-[inset_0_0_22px_rgba(16,185,129,0.35),0_0_20px_rgba(52,211,153,0.2)]"
                        : "text-slate-400 hover:border-cyan-400/50 hover:text-cyan-200/90 hover:shadow-[0_0_18px_rgba(6,182,212,0.15)]"
                    )}
                    whileHover={{ scale: actionLoading ? 1 : 1.03 }}
                    whileTap={{ scale: 0.96 }}
                  >
                    <span className="text-sm">{action.icon}</span>
                    <span className="max-w-[3.5rem] px-0.5 text-center leading-tight">
                      {isOn ? c.dashboard.quickToggleOn : action.label}
                    </span>
                  </motion.button>
                );
              })}
            </div>
            <div className="relative z-10 shrink-0 border border-cyan-500/25 bg-black/55 p-2.5 shadow-[inset_0_0_0_1px_rgba(6,182,212,0.06)]">
              <p className="mb-1.5 font-mono text-[9px] uppercase tracking-wider text-slate-400">{c.dashboard.vadHeading}</p>
              <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-center">
                {!isVoiceCaptureRunning ? (
                  <button
                    type="button"
                    onClick={async () => {
                      try {
                        await invoke("start_voice_capture");
                        setIsVoiceCaptureRunning(true);
                      } catch (e) {
                        console.error(e);
                      }
                    }}
                    className="flex w-full items-center justify-center gap-1.5 border border-amber-500/45 bg-black/50 px-3 py-2 font-mono text-[9px] font-medium uppercase tracking-wider text-amber-300 transition-all [clip-path:polygon(0_0,calc(100%-8px)_0,100%_8px,100%_100%,0_100%)] hover:shadow-[inset_0_0_16px_rgba(245,158,11,0.2)] active:shadow-[inset_0_0_20px_rgba(6,182,212,0.45)] sm:w-auto"
                  >
                    <span>🎤</span> {c.dashboard.vadStart}
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={async () => {
                      try {
                        await invoke("stop_voice_capture");
                        setIsVoiceCaptureRunning(false);
                      } catch (e) {
                        console.error(e);
                      }
                    }}
                    className="flex w-full items-center justify-center gap-1.5 border border-rose-500/45 bg-black/50 px-3 py-2 font-mono text-[9px] font-medium uppercase tracking-wider text-rose-300 transition-all [clip-path:polygon(0_0,calc(100%-8px)_0,100%_8px,100%_100%,0_100%)] hover:shadow-[inset_0_0_16px_rgba(244,63,94,0.2)] active:shadow-[inset_0_0_20px_rgba(6,182,212,0.45)] sm:w-auto"
                  >
                    <span>⏹</span> {c.dashboard.vadStop}
                  </button>
                )}
                {isVoiceCaptureRunning && (
                  <span className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-wider text-amber-400">
                    <span className="h-1.5 w-1.5 animate-pulse bg-amber-400 shadow-[0_0_8px_rgba(251,191,36,0.9)]" />
                    {c.dashboard.vadCapturing}
                  </span>
                )}
              </div>
            </div>
          </div>
        </motion.div>
      </section>

      {/* Bottom: Agenda — flex-1 保证在常见视口下可见；卡片区可横/纵滚动 */}
      <section className="flex min-h-[200px] flex-1 flex-col py-1">
        <div className="console-fiber-host console-holo-slab flex min-h-0 flex-1 flex-col overflow-hidden">
          <div className="flex flex-shrink-0 flex-col gap-1 border-b border-cyan-500/20 px-4 py-3 sm:flex-row sm:items-end sm:justify-between">
            <div className="min-w-0">
              <span
                className="font-mono text-[10px] font-semibold uppercase tracking-[0.28em] text-cyan-600/90"
                style={{ fontFamily: "Orbitron, sans-serif" }}
              >
                {c.dashboard.agendaTitle}
              </span>
              <p className="mt-1 max-w-2xl font-mono text-[9px] uppercase leading-relaxed tracking-wider text-slate-600">
                {c.dashboard.agendaSubtitle}
              </p>
            </div>
            <span className="shrink-0 font-mono text-[10px] tabular-nums text-cyan-700/80">
              {c.dashboard.agendaStat.replace("{n}", String(displaySuggestions.length))}
            </span>
          </div>
          <div className="custom-scrollbar flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto overflow-x-auto px-4 py-4 sm:flex-row sm:gap-8">
            {displaySuggestions.length === 0 ? (
              <p className="font-mono text-xs uppercase tracking-wider text-slate-600">{c.dashboard.agendaEmpty}</p>
            ) : (
              displaySuggestions.map((s, i) => (
                <motion.div
                  key={s.id}
                  initial={{ opacity: 0, x: -16 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: 0.05 + i * 0.04 }}
                  className="flex w-full min-w-[min(18rem,85vw)] max-w-md shrink-0 flex-col justify-between border-l-2 border-cyan-500/35 bg-black/35 py-4 pl-5 pr-4 shadow-[inset_0_0_0_1px_rgba(6,182,212,0.06),8px_0_32px_rgba(0,0,0,0.35)] backdrop-blur-md transition-all hover:border-cyan-400/55 hover:shadow-[0_0_24px_rgba(6,182,212,0.08)] sm:min-w-[17rem]"
                >
                  {s.type ? (
                    <span className="mb-2 inline-flex w-fit border border-cyan-500/25 bg-cyan-950/40 px-2 py-0.5 font-mono text-[9px] uppercase tracking-widest text-cyan-500/90">
                      {s.type}
                    </span>
                  ) : null}
                  <p className="mb-4 line-clamp-4 font-mono text-sm leading-relaxed text-slate-300 [text-shadow:0_0_16px_rgba(0,0,0,0.8)]">
                    {s.text}
                  </p>
                  <button
                    type="button"
                    onClick={() => handleSuggestionAction(s.id, s.action)}
                    className="self-start border border-rose-500/40 bg-rose-500/10 px-4 py-2 font-mono text-[10px] font-semibold uppercase tracking-wider text-rose-300 transition-all [clip-path:polygon(0_0,calc(100%-8px)_0,100%_8px,100%_100%,8px_100%,0_calc(100%-8px))] hover:bg-rose-500/20 active:shadow-[inset_0_0_18px_rgba(6,182,212,0.35)]"
                  >
                    {c.suggestionActionLabels[s.action] ?? s.action}
                  </button>
                </motion.div>
              ))
            )}
          </div>
        </div>
      </section>
    </div>
  );
}
