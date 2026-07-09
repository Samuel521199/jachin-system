/**
 * Dashboard - Jachin Omni Cockpit
 * Personal assistant first, diagnostics second.
 */

import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { motion } from "framer-motion";
import { invoke } from "@tauri-apps/api/core";
import {
  Activity,
  BrainCircuit,
  ChevronDown,
  Cpu,
  Database,
  Eye,
  LockKeyhole,
  Mic,
  Moon,
  Network,
  Send,
  ShieldCheck,
  Sparkles,
  Square,
  Trash2,
  Zap,
} from "lucide-react";
import { MindStream } from "../components/MindStream";
import { ComputeTopology } from "../components/ComputeTopology";
import { JachinCore } from "../../components/Omni/JachinCore";
import { getClusterStats, getSuggestions, getLogsRecent, executeSuggestion, getGpuStats, getClusterNodes, getClusterTasks } from "../../lib/api";
import type { SuggestionItem, GpuStatsItem, ClusterNodeInfo, ClusterTaskInfo } from "../../lib/api";
import type { CoreVisualState } from "../../hooks/useJachinCoreState";
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

const formatPercent = (value: number) => `${Math.max(0, Math.min(100, Math.round(value)))}%`;

export function Dashboard({
  suggestions: suggestionsProp,
  onSuggestionAction,
}: {
  suggestions?: SuggestionItem[];
  onSuggestionAction?: (suggestionId: string, action: string) => void;
} = {}) {
  const [lang] = useDesktopUiLang();
  const c = useMemo(() => getDesktopConsole(lang), [lang]);
  const copy = useMemo(
    () =>
      lang === "zh"
        ? {
            eyebrow: "OMNI COCKPIT",
            title: "Jachin Omni",
            subtitle: "个人智能副驾驶已就绪",
            placeholder: "交给 Jachin 一件事...",
            statusIdle: "待命",
            statusThinking: "思考中",
            statusStreaming: "执行中",
            actions: "快捷控制",
            autonomy: "当前态势",
            trust: "信任层",
            suggestions: "今天可以为你处理",
            systems: "系统层",
            stream: "思维流",
            topology: "算力拓扑",
            openSystems: "展开系统层",
            closeSystems: "收起系统层",
            localOnly: "本地优先",
            guarded: "安全锁在线",
            memory: "记忆",
            devices: "设备",
            running: "任务",
            sendTitle: "打开 Omni 对话",
            voiceStartTitle: "开始语音采集",
            voiceStopTitle: "停止语音采集",
          }
        : {
            eyebrow: "OMNI COCKPIT",
            title: "Jachin Omni",
            subtitle: "Personal AI copilot ready",
            placeholder: "Give Jachin something to handle...",
            statusIdle: "Idle",
            statusThinking: "Thinking",
            statusStreaming: "Executing",
            actions: "Quick Controls",
            autonomy: "Situation",
            trust: "Trust Layer",
            suggestions: "For Today",
            systems: "Systems",
            stream: "Mind Stream",
            topology: "Compute Topology",
            openSystems: "Open systems",
            closeSystems: "Close systems",
            localOnly: "Local first",
            guarded: "Safety lock online",
            memory: "Memory",
            devices: "Devices",
            running: "Tasks",
            sendTitle: "Open Omni chat",
            voiceStartTitle: "Start voice capture",
            voiceStopTitle: "Stop voice capture",
          },
    [lang],
  );

  const quickActions = useMemo(
    () =>
      [
        {
          id: "privacy",
          label: c.dashboard.quickPrivacy,
          Icon: ShieldCheck,
          cmd: "quick_action_privacy_mode",
          isToggle: true as const,
          title: c.dashboard.quickPrivacyTitle,
        },
        {
          id: "clean",
          label: c.dashboard.quickClean,
          Icon: Trash2,
          cmd: "quick_action_clear_memory",
          isToggle: false as const,
          title: c.dashboard.quickCleanTitle,
        },
        {
          id: "eagle",
          label: c.dashboard.quickEagle,
          Icon: Eye,
          cmd: "quick_action_eagle_eye",
          isToggle: true as const,
          title: c.dashboard.quickEagleTitle,
        },
        {
          id: "sleep",
          label: c.dashboard.quickSleep,
          Icon: Moon,
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
  const [clusterStats, setClusterStats] = useState<{ nodes?: { total?: number; online?: number }; tasks?: { running?: number; total?: number; pending?: number } } | null>(null);
  const [gpuStats, setGpuStats] = useState<{ gpus: GpuStatsItem[] } | null>(null);
  const [clusterNodes, setClusterNodes] = useState<ClusterNodeInfo[]>([]);
  const [clusterTasks, setClusterTasks] = useState<ClusterTaskInfo[]>([]);
  const [isVoiceCaptureRunning, setIsVoiceCaptureRunning] = useState(false);
  const [commandInput, setCommandInput] = useState("");
  const [systemsOpen, setSystemsOpen] = useState(false);

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
    [onSuggestionAction],
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
  const runningTasks = clusterStats?.tasks?.running ?? 0;
  const totalTasks = clusterStats?.tasks?.total ?? 0;
  const onlineNodes = clusterStats?.nodes?.online ?? 0;
  const totalNodes = clusterStats?.nodes?.total ?? 0;
  const gpuPercent = gpuStats?.gpus?.[0]?.utilization_gpu ?? 0;
  const coreState: CoreVisualState = isVoiceCaptureRunning || actionLoading
    ? "thinking"
    : runningTasks > 0
      ? "streaming"
      : "idle";
  const statusLabel = coreState === "thinking"
    ? copy.statusThinking
    : coreState === "streaming"
      ? copy.statusStreaming
      : copy.statusIdle;

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

  const toggleVoiceCapture = async () => {
    try {
      if (isVoiceCaptureRunning) {
        await invoke("stop_voice_capture");
        setIsVoiceCaptureRunning(false);
      } else {
        await invoke("start_voice_capture");
        setIsVoiceCaptureRunning(true);
      }
    } catch (e) {
      console.error(e);
    }
  };

  const openOmniChat = async () => {
    try {
      if (commandInput.trim()) {
        window.localStorage.setItem("jachin_console_last_prompt", commandInput.trim());
      }
      await invoke("show_chat_window");
      setCommandInput("");
    } catch (e) {
      console.error(e);
    }
  };

  return (
    <div className="omni-cockpit flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-4 sm:p-5 xl:p-6">
      <section className="grid min-h-[430px] grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_21rem]">
        <motion.div
          className="console-orb-panel jarvis-panel jarvis-hero-panel relative flex min-h-[430px] flex-col overflow-hidden p-5 sm:p-6"
          initial={{ opacity: 0, y: 14 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.38 }}
        >
          <div className="jarvis-hero-grid" aria-hidden />
          <div className="jarvis-corner jarvis-corner-tl" aria-hidden />
          <div className="jarvis-corner jarvis-corner-tr" aria-hidden />
          <div className="jarvis-corner jarvis-corner-bl" aria-hidden />
          <div className="jarvis-corner jarvis-corner-br" aria-hidden />
          <div className="pointer-events-none absolute inset-x-12 top-0 h-px bg-gradient-to-r from-transparent via-cyan-200/55 to-transparent" />
          <div className="relative z-10 flex flex-1 flex-col items-center justify-center gap-6 text-center">
            <div className="flex items-center gap-2 rounded-full border border-cyan-300/15 bg-cyan-300/[0.04] px-3 py-1 text-[10px] font-medium uppercase tracking-[0.24em] text-cyan-100/75">
              <Sparkles className="h-3.5 w-3.5 text-cyan-200/85" />
              {copy.eyebrow}
            </div>

            <div className="jarvis-core-stage relative flex h-44 w-44 items-center justify-center sm:h-52 sm:w-52">
              <svg className="jarvis-core-svg" viewBox="0 0 260 260" aria-hidden>
                <circle className="jarvis-core-ring jarvis-core-ring-outer" cx="130" cy="130" r="108" />
                <circle className="jarvis-core-ring jarvis-core-ring-mid" cx="130" cy="130" r="86" />
                <circle className="jarvis-core-ring jarvis-core-ring-inner" cx="130" cy="130" r="63" />
                <path className="jarvis-core-arc jarvis-core-arc-a" d="M130 22a108 108 0 0 1 99 65" />
                <path className="jarvis-core-arc jarvis-core-arc-b" d="M51 204a108 108 0 0 1 0-148" />
                <path className="jarvis-core-arc jarvis-core-arc-c" d="M206 206a108 108 0 0 1-120 23" />
                <line className="jarvis-core-line" x1="130" y1="18" x2="130" y2="48" />
                <line className="jarvis-core-line" x1="130" y1="212" x2="130" y2="242" />
                <line className="jarvis-core-line" x1="18" y1="130" x2="48" y2="130" />
                <line className="jarvis-core-line" x1="212" y1="130" x2="242" y2="130" />
              </svg>
              <div className="jarvis-core-scan" aria-hidden />
              <div className="absolute inset-0 rounded-full bg-cyan-300/[0.025] blur-2xl" />
              <JachinCore
                state={coreState}
                machineState={coreState === "streaming" ? "STREAMING" : coreState === "thinking" ? "THINKING" : "IDLE"}
                toolFlash={null}
                className="!h-24 !w-24"
              />
            </div>

            <div className="space-y-2">
              <h1 className="text-3xl font-semibold tracking-normal text-cyan-50 sm:text-5xl">
                {copy.title}
              </h1>
              <p className="text-sm leading-relaxed text-slate-300/82 sm:text-base">
                {copy.subtitle}
              </p>
            </div>

            <div className="w-full max-w-3xl rounded-[8px] border border-cyan-200/[0.08] bg-slate-950/45 p-2 shadow-[0_20px_70px_rgba(0,0,0,0.34),inset_0_0_24px_rgba(56,189,248,0.035)] backdrop-blur-xl">
              <form
                className="flex items-center gap-2"
                onSubmit={(e) => {
                  e.preventDefault();
                  void openOmniChat();
                }}
              >
                <BrainCircuit className="ml-2 h-5 w-5 shrink-0 text-cyan-200/70" />
                <input
                  value={commandInput}
                  onChange={(e) => setCommandInput(e.target.value)}
                  placeholder={copy.placeholder}
                  className="h-12 min-w-0 flex-1 bg-transparent text-base text-cyan-50 placeholder:text-slate-500 focus:outline-none"
                />
                <button
                  type="button"
                  onClick={() => void toggleVoiceCapture()}
                  title={isVoiceCaptureRunning ? copy.voiceStopTitle : copy.voiceStartTitle}
                  aria-label={isVoiceCaptureRunning ? copy.voiceStopTitle : copy.voiceStartTitle}
                  className={cn(
                    "flex h-10 w-10 shrink-0 items-center justify-center rounded-[8px] border transition",
                    isVoiceCaptureRunning
                      ? "border-amber-300/35 bg-amber-300/15 text-amber-200"
                      : "border-cyan-200/[0.08] bg-cyan-300/[0.025] text-slate-300 hover:border-cyan-200/[0.16] hover:bg-cyan-300/[0.055] hover:text-cyan-100",
                  )}
                >
                  {isVoiceCaptureRunning ? <Square className="h-4 w-4" /> : <Mic className="h-4 w-4" />}
                </button>
                <button
                  type="submit"
                  title={copy.sendTitle}
                  aria-label={copy.sendTitle}
                  className="flex h-10 w-10 shrink-0 items-center justify-center rounded-[8px] border border-cyan-200/[0.14] bg-cyan-300/[0.075] text-cyan-100 transition hover:bg-cyan-300/[0.12]"
                >
                  <Send className="h-4 w-4" />
                </button>
              </form>
            </div>

            <div className="grid w-full max-w-3xl grid-cols-2 gap-2 sm:grid-cols-4">
              {[
                { label: "CPU", value: formatPercent(cpuPercent), Icon: Cpu, tone: "text-cyan-100" },
                { label: "RAM", value: formatPercent(ramPercent), Icon: Database, tone: "text-violet-100" },
                { label: "GPU", value: formatPercent(gpuPercent), Icon: Zap, tone: "text-amber-100" },
                { label: "RAY", value: `${onlineNodes}/${totalNodes || "—"}`, Icon: Network, tone: "text-emerald-100" },
              ].map((item) => (
                <div key={item.label} className="jarvis-tile rounded-[8px] border border-cyan-200/[0.07] bg-cyan-300/[0.022] px-3 py-3 text-left shadow-[inset_0_0_18px_rgba(56,189,248,0.018)]">
                  <div className="mb-3 flex items-center justify-between text-slate-500">
                    <span className="text-[10px] font-medium uppercase tracking-[0.18em]">{item.label}</span>
                    <item.Icon className="h-4 w-4" />
                  </div>
                  <p className={cn("font-mono text-xl tabular-nums", item.tone)}>{item.value}</p>
                </div>
              ))}
            </div>
          </div>
        </motion.div>

        <motion.aside
          className="flex min-h-[430px] flex-col gap-4"
          initial={{ opacity: 0, y: 14 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.38, delay: 0.06 }}
        >
          <div className="console-soft-panel jarvis-panel flex flex-col gap-4 p-4">
            <div className="flex items-center justify-between">
              <h2 className="text-xs font-semibold uppercase tracking-[0.2em] text-cyan-100/75">{copy.autonomy}</h2>
              <span className="inline-flex items-center gap-1.5 rounded-full border border-cyan-200/14 bg-cyan-300/[0.05] px-2.5 py-1 text-[10px] text-cyan-100/80">
                <Activity className="h-3.5 w-3.5" />
                {statusLabel}
              </span>
            </div>
            <div className="grid gap-2">
              <StatusRow Icon={LockKeyhole} label={copy.trust} value={privacyMode ? c.dashboard.quickPrivacy : copy.guarded} active={Boolean(privacyMode)} />
              <StatusRow Icon={BrainCircuit} label={copy.running} value={`${runningTasks}/${totalTasks || 0}`} active={runningTasks > 0} />
              <StatusRow Icon={Network} label={copy.devices} value={`${onlineNodes || 0} online`} active={onlineNodes > 0} />
            </div>
          </div>

          <div className="console-soft-panel jarvis-panel flex flex-1 flex-col gap-4 p-4">
            <h2 className="text-xs font-semibold uppercase tracking-[0.2em] text-cyan-100/75">{copy.actions}</h2>
            <div className="grid grid-cols-2 gap-2">
              {quickActions.map((action) => {
                const isOn =
                  (action.id === "privacy" && privacyMode) ||
                  (action.id === "eagle" && eagleEyeOn) ||
                  (action.id === "sleep" && hibernateOn);
                return (
                  <button
                    key={action.id}
                    type="button"
                    disabled={actionLoading !== null}
                    onClick={() => void runQuickAction(action.cmd, action.isToggle)}
                    title={action.title}
                    className={cn(
                    "group flex min-h-[82px] flex-col items-start justify-between rounded-[8px] border p-3 text-left transition disabled:opacity-55",
                      isOn
                        ? "border-emerald-300/30 bg-emerald-300/[0.08] text-emerald-100"
                        : "border-cyan-200/[0.07] bg-cyan-300/[0.022] text-slate-300 hover:border-cyan-200/[0.14] hover:bg-cyan-300/[0.05]",
                    )}
                  >
                    <action.Icon className={cn("h-5 w-5", isOn ? "text-emerald-200" : "text-cyan-100/72")} />
                    <span className="text-sm font-medium leading-tight">{isOn ? c.dashboard.quickToggleOn : action.label}</span>
                  </button>
                );
              })}
            </div>
            <div className="mt-auto rounded-[8px] border border-cyan-200/[0.065] bg-slate-950/28 p-3">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-slate-500">{c.dashboard.vadHeading}</p>
                  <p className="mt-1 text-xs text-slate-300/75">{isVoiceCaptureRunning ? c.dashboard.vadCapturing : copy.statusIdle}</p>
                </div>
                <button
                  type="button"
                  onClick={() => void toggleVoiceCapture()}
                  title={isVoiceCaptureRunning ? copy.voiceStopTitle : copy.voiceStartTitle}
                  aria-label={isVoiceCaptureRunning ? copy.voiceStopTitle : copy.voiceStartTitle}
                  className={cn(
                    "flex h-10 w-10 items-center justify-center rounded-[8px] border transition",
                    isVoiceCaptureRunning
                      ? "border-amber-300/35 bg-amber-300/15 text-amber-200"
                      : "border-cyan-200/[0.08] bg-cyan-300/[0.025] text-slate-300 hover:bg-cyan-300/[0.055] hover:text-cyan-100",
                  )}
                >
                  {isVoiceCaptureRunning ? <Square className="h-4 w-4" /> : <Mic className="h-4 w-4" />}
                </button>
              </div>
            </div>
          </div>
        </motion.aside>
      </section>

      <section className="grid min-h-[260px] grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_23rem]">
        <motion.div
          className="console-soft-panel jarvis-panel flex min-h-0 flex-col overflow-hidden"
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.35, delay: 0.1 }}
        >
          <div className="flex items-center justify-between border-b border-cyan-200/[0.06] px-4 py-3">
            <div>
              <h2 className="text-xs font-semibold uppercase tracking-[0.2em] text-cyan-100/75">{copy.suggestions}</h2>
              <p className="mt-1 text-xs text-slate-500">{c.dashboard.agendaStat.replace("{n}", String(displaySuggestions.length))}</p>
            </div>
            <Sparkles className="h-4 w-4 text-cyan-100/55" />
          </div>
          <div className="custom-scrollbar flex min-h-0 flex-1 gap-3 overflow-x-auto px-4 py-4">
            {displaySuggestions.length === 0 ? (
              <p className="text-sm text-slate-500">{c.dashboard.agendaEmpty}</p>
            ) : (
              displaySuggestions.slice(0, 6).map((s, i) => (
                <motion.article
                  key={s.id}
                  initial={{ opacity: 0, x: -10 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: 0.12 + i * 0.035 }}
                  className="jarvis-tile flex min-w-[17rem] max-w-[19rem] flex-col justify-between rounded-[8px] border border-cyan-200/[0.07] bg-cyan-300/[0.022] p-4 shadow-[inset_0_0_18px_rgba(56,189,248,0.018)]"
                >
                  <div>
                    {s.type ? (
                      <span className="mb-3 inline-flex rounded-full border border-cyan-200/12 bg-cyan-300/[0.055] px-2.5 py-1 text-[10px] font-medium uppercase tracking-[0.16em] text-cyan-100/75">
                        {s.type}
                      </span>
                    ) : null}
                    <p className="line-clamp-4 text-sm leading-relaxed text-slate-200/90">{s.text}</p>
                  </div>
                  <button
                    type="button"
                    onClick={() => void handleSuggestionAction(s.id, s.action)}
                    className="mt-5 inline-flex w-fit items-center gap-2 rounded-[8px] border border-cyan-200/[0.1] bg-cyan-300/[0.045] px-3 py-2 text-xs font-medium text-cyan-100 transition hover:border-cyan-200/[0.18] hover:bg-cyan-300/[0.08]"
                  >
                    <Zap className="h-3.5 w-3.5" />
                    {c.suggestionActionLabels[s.action] ?? s.action}
                  </button>
                </motion.article>
              ))
            )}
          </div>
        </motion.div>

        <motion.div
          className="console-soft-panel jarvis-panel flex min-h-[260px] flex-col overflow-hidden"
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.35, delay: 0.14 }}
        >
          <button
            type="button"
            onClick={() => setSystemsOpen((v) => !v)}
            className="flex items-center justify-between border-b border-cyan-200/[0.06] px-4 py-3 text-left transition hover:bg-cyan-300/[0.025]"
          >
            <div>
              <h2 className="text-xs font-semibold uppercase tracking-[0.2em] text-cyan-100/75">{copy.systems}</h2>
              <p className="mt-1 text-xs text-slate-500">{systemsOpen ? copy.closeSystems : copy.openSystems}</p>
            </div>
            <ChevronDown className={cn("h-4 w-4 text-cyan-100/60 transition", systemsOpen && "rotate-180")} />
          </button>
          <div className="min-h-0 flex-1 overflow-hidden p-4">
            {systemsOpen ? (
              <ComputeTopology
                compact
                workerCount={Math.max(0, (clusterStats?.nodes?.total ?? 1) - 1)}
                activeWorkerIndex={runningTasks > 0 ? 0 : (cpuPercent > 25 ? 0 : -1)}
                cpuPercent={cpuPercent}
                ramPercent={ramPercent}
                gpuStats={gpuStats?.gpus ?? undefined}
                nodes={clusterNodes}
                tasks={clusterTasks}
              />
            ) : (
              <div className="grid h-full content-center gap-3">
                <StatusMeter label="CPU" value={cpuPercent} />
                <StatusMeter label="RAM" value={ramPercent} />
                <StatusMeter label="GPU" value={gpuPercent} />
              </div>
            )}
          </div>
        </motion.div>
      </section>

      <section className="min-h-[210px] shrink-0">
        <div className="console-soft-panel jarvis-panel h-full overflow-hidden p-4">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-xs font-semibold uppercase tracking-[0.2em] text-cyan-100/75">{copy.stream}</h2>
            <span className="rounded-full border border-emerald-300/14 bg-emerald-300/[0.055] px-2.5 py-1 text-[10px] text-emerald-100/75">
              {c.mind.statusLive}
            </span>
          </div>
          <MindStream
            className="h-[170px] min-h-0"
            maxLines={5}
            demoLoop
            liveStatsLines={liveStatsLines}
            liveLogLines={liveLogLines}
            mindLocale={c.mind}
            localizeLine={(line) => localizeMindStreamLine(line, lang)}
          />
        </div>
      </section>
    </div>
  );
}

function StatusRow({
  Icon,
  label,
  value,
  active,
}: {
  Icon: typeof Activity;
  label: string;
  value: string;
  active?: boolean;
}) {
  return (
    <div className="flex items-center gap-3 rounded-[8px] border border-cyan-200/[0.07] bg-cyan-300/[0.022] px-3 py-3 shadow-[inset_0_0_18px_rgba(56,189,248,0.018)]">
      <div
        className={cn(
          "flex h-9 w-9 shrink-0 items-center justify-center rounded-[8px] border",
          active ? "border-cyan-200/[0.14] bg-cyan-300/[0.065] text-cyan-100" : "border-cyan-200/[0.07] bg-cyan-300/[0.022] text-slate-400",
        )}
      >
        <Icon className="h-4 w-4" />
      </div>
      <div className="min-w-0 flex-1">
        <p className="truncate text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-500">{label}</p>
        <p className="mt-0.5 truncate text-sm text-slate-200/88">{value}</p>
      </div>
    </div>
  );
}

function StatusMeter({ label, value }: { label: string; value: number }) {
  const safeValue = Math.max(0, Math.min(100, Math.round(value)));
  return (
    <div>
      <div className="mb-2 flex items-center justify-between text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-500">
        <span>{label}</span>
        <span className="font-mono text-cyan-100/75">{safeValue}%</span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-white/[0.055]">
        <div
          className="h-full rounded-full bg-gradient-to-r from-cyan-300/70 via-sky-200/80 to-emerald-200/75"
          style={{ width: `${safeValue}%` }}
        />
      </div>
    </div>
  );
}
