/**
 * Dashboard - 战情室 (Situation Room)
 * Top 30% MindStream | Middle 40% ComputeTopology + Quick Actions | Bottom 30% ProactiveSuggestions
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
    <div className="flex-1 flex flex-col min-h-0 p-6 gap-6">
      {/* Top 30%: Mind Stream */}
      <section className="h-[30%] min-h-[200px] flex-shrink-0">
        <MindStream
          className="h-full"
          maxLines={6}
          demoLoop
          liveStatsLines={liveStatsLines}
          liveLogLines={liveLogLines}
          mindLocale={c.mind}
          localizeLine={(line) => localizeMindStreamLine(line, lang)}
        />
      </section>

      {/* Middle 40%: Compute Topology + Quick Actions */}
      <section className="flex-[4] min-h-0 flex gap-6">
        <motion.div
          layout
          className="flex-1 glass-panel rounded-xl p-6 flex flex-col items-center justify-center min-h-0"
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.35 }}
        >
          <ComputeTopology
            workerCount={Math.max(0, (clusterStats?.nodes?.total ?? 1) - 1)}
            activeWorkerIndex={(clusterStats?.tasks?.running ?? 0) > 0 ? 0 : (cpuPercent > 25 ? 0 : -1)}
            cpuPercent={cpuPercent}
            ramPercent={ramPercent}
            gpuStats={gpuStats?.gpus ?? undefined}
            nodes={clusterNodes}
            tasks={clusterTasks}
          />
        </motion.div>
        <motion.div
          layout
          className="w-80 flex-shrink-0 glass-panel rounded-xl p-4 flex flex-col min-h-0"
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.35, delay: 0.05 }}
        >
          <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-400 mb-3" style={{ fontFamily: "Orbitron, sans-serif" }}>
            {c.dashboard.quickActionsTitle}
          </h2>
          <div className="grid grid-cols-2 gap-2">
            {quickActions.map((action, i) => {
              const isOn = (action.id === "privacy" && privacyMode) || (action.id === "eagle" && eagleEyeOn) || (action.id === "sleep" && hibernateOn);
              return (
                <motion.button
                  key={action.id}
                  type="button"
                  disabled={actionLoading !== null}
                  onClick={() => runQuickAction(action.cmd, action.isToggle)}
                  title={action.title}
                  className={cn(
                    "flex flex-col items-center justify-center gap-1 py-3 px-2 rounded-lg border text-xs font-medium transition-colors disabled:opacity-60",
                    isOn ? "bg-emerald-500/20 border-emerald-500/40 text-emerald-300" : "bg-white/5 border-white/10 text-slate-300 hover:bg-rose-500/20 hover:border-rose-500/40"
                  )}
                  whileHover={{ scale: actionLoading ? 1 : 1.02 }}
                  whileTap={{ scale: 0.98 }}
                >
                  <span className="text-lg">{action.icon}</span>
                  <span className="leading-tight">{isOn ? c.dashboard.quickToggleOn : action.label}</span>
                </motion.button>
              );
            })}
          </div>
          <div className="mt-4 pt-4 border-t border-white/10">
            <p className="text-xs text-slate-500 mb-2">{c.dashboard.vadHeading}</p>
            <div className="flex items-center gap-2">
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
                  className="px-3 py-2 rounded-lg border border-amber-500/40 bg-amber-500/15 text-amber-300 text-xs font-medium hover:bg-amber-500/25 flex items-center gap-1.5"
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
                  className="px-3 py-2 rounded-lg border border-rose-500/40 bg-rose-500/15 text-rose-300 text-xs font-medium hover:bg-rose-500/25 flex items-center gap-1.5"
                >
                  <span>⏹</span> {c.dashboard.vadStop}
                </button>
              )}
              {isVoiceCaptureRunning && (
                <span className="text-xs text-amber-400 flex items-center gap-1">
                  <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />
                  {c.dashboard.vadCapturing}
                </span>
              )}
            </div>
          </div>
        </motion.div>
      </section>

      {/* Bottom 30%: Proactive Suggestions */}
      <section className="h-[30%] min-h-[140px] flex-shrink-0">
        <div className="h-full flex flex-col glass-panel rounded-xl overflow-hidden">
          <div className="flex-shrink-0 px-4 py-2 border-b border-white/10 flex items-center gap-2">
            <span className="text-xs font-semibold uppercase tracking-wider text-slate-400" style={{ fontFamily: "Orbitron, sans-serif" }}>
              {c.dashboard.agendaTitle}
            </span>
          </div>
          <div className="flex-1 overflow-x-auto overflow-y-hidden flex gap-4 p-4 custom-scrollbar">
            {displaySuggestions.map((s, i) => (
              <motion.div
                key={s.id}
                layout
                initial={{ opacity: 0, x: -20 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: 0.1 + i * 0.05 }}
                className="flex-shrink-0 w-72 rounded-xl border border-white/10 bg-white/5 p-4 flex flex-col justify-between hover:bg-white/10 hover:border-rose-500/20 transition-colors"
              >
                <p className="text-sm text-slate-300 mb-3 line-clamp-2">{s.text}</p>
                <button
                  type="button"
                  onClick={() => handleSuggestionAction(s.id, s.action)}
                  className="self-start px-3 py-1.5 rounded-lg bg-rose-500/20 text-rose-400 text-xs font-medium border border-rose-500/30 hover:bg-rose-500/30 transition-colors"
                >
                  {c.suggestionActionLabels[s.action] ?? s.action}
                </button>
              </motion.div>
            ))}
          </div>
        </div>
      </section>
    </div>
  );
}
