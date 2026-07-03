/**
 * Skill Matrix - 军械库：网格磁贴 + 自然语言执行 + 悬停 Permission X-Ray
 */

import { useState, useEffect, useCallback } from "react";
import { motion } from "framer-motion";
import { Play, Loader2, RefreshCw, Trash2, RotateCcw, EyeOff, Plug } from "lucide-react";
import { invoke } from "@tauri-apps/api/core";
import {
  listSkills,
  executeSkill,
  executeSkillStream,
  isHrSkill,
  invokePlugin,
  uninstallSkill,
  hideSkill,
  unhideSkill,
  listHiddenSkills,
  listL3Mcps,
  hideMcp,
  unhideMcp,
  deleteMcp,
  listHiddenMcps,
  listRecycleBinSkills,
  restoreRecycleBinSkill,
  permanentDeleteRecycleBinSkill,
  SkillInfo,
  RecycleBinItem,
  L3McpInfo,
  BACKEND_URL,
} from "../../lib/api";
import { INVENTORY_UPDATED_EVENT } from "../../hooks/useUISyncEventSource";
import { LiveTile } from "../components/LiveTile";
import { SkillDetailModal } from "../components/SkillDetailModal";
import { MarkdownMessage } from "../../components/Chat/MarkdownMessage";
import { SkillChainView, type ChainStep } from "../components/SkillChainView";
import { UninstallSkillModal } from "../components/UninstallSkillModal";
import { SkillSettingsDrawer } from "../components/SkillSettingsDrawer";
import { BatchProgressBar } from "../components/BatchProgressBar";
import { AnimatePresence } from "framer-motion";

type CapabilityInstallStatus =
  | "installed"
  | "local_only"
  | "update_available"
  | "repair_needed"
  | "disabled"
  | "not_installed"
  | "blocked";

interface CapabilityInstallItem {
  id: string;
  name: string;
  kind: string;
  enabled: boolean;
  status: CapabilityInstallStatus | string;
}

type BusinessSkillDefinition = {
  id: string;
  name: string;
  description: string;
  aliases?: readonly string[];
  prefixes?: readonly string[];
  nameIncludes?: readonly string[];
};

const READY_CAPABILITY_STATUSES = new Set(["installed", "local_only", "update_available"]);
const DEV_RUNTIME = import.meta.env.DEV;

const BUSINESS_SKILLS: readonly BusinessSkillDefinition[] = [
  {
    id: "com.jachin.skill.bi-growth-officer",
    name: "BI 数据增长官",
    description: "每天自动生成经营分析、留存、充值、游戏经济和战略建议，重点关注在线增长。",
    aliases: ["com.jachin.bi.analysis"],
    prefixes: ["com.jachin.skill.bi"],
    nameIncludes: ["bi 数据增长官", "bi 分析"],
  },
  {
    id: "com.jachin.skill.pmo-copilot",
    name: "PMO 项目治理中枢",
    description: "自动读飞书多维表，生成项目战报和变更风险预警。",
    aliases: ["pmo-copilot", "com.jachin.pmo.copilot"],
    prefixes: ["com.jachin.skill.pmo", "pmo-"],
    nameIncludes: ["pmo copilot", "项目治理"],
  },
  {
    id: "com.jachin.skill.ai-recruiting-director",
    name: "AI 招聘总监",
    description: "Boss 发帖、打招呼、收简历、简历透析和飞书遥控。",
    aliases: ["hr-recruitment", "com.jachin.hr.recruitment", "jpp:com.jachin.hr.analyzer4"],
    prefixes: ["com.jachin.skill.hr", "com.jachin.skill.recruit", "hr-"],
    nameIncludes: ["ai 招聘总监", "hr 招聘", "招聘总监", "hr 透析"],
  },
  {
    id: "com.jachin.skill.desktop-execution-agent",
    name: "企业桌面执行 Agent",
    description: "跨 Windows、飞书、文件、浏览器和办公软件完成真实任务，并保留证据链。",
    aliases: ["com.jachin.system.pilot", "com.jachin.os-mate"],
    prefixes: ["com.jachin.skill.desktop", "com.jachin.skill.os"],
    nameIncludes: ["桌面执行", "os assistant", "system pilot"],
  },
  {
    id: "com.jachin.skill.game-qa-automation",
    name: "游戏 QA / 自动化测试平台",
    description: "面向游戏和复杂 UI 的视觉测试、冒烟、回放和规则执行。",
    aliases: ["com.jachin.mcp.gameqa", "gameqa", "gameqa_mcp", "com.jachin.k11.smoke"],
    prefixes: ["com.jachin.skill.gameqa", "com.jachin.gameqa", "gameqa"],
    nameIncludes: ["gameqa", "游戏测试", "自动化测试"],
  },
  {
    id: "com.jachin.skill.english-learning-assistant",
    name: "英语助手",
    description: "右下角轻量背词、例句精讲、词义查询、翻译和学习统计。",
    prefixes: ["com.jachin.skill.english"],
    nameIncludes: ["english learning assistant", "英语助手"],
  },
];

function normalizeId(raw: string | undefined | null): string {
  return (raw ?? "").trim().toLowerCase();
}

function matchesBusinessDefinition(
  id: string | undefined | null,
  name: string | undefined | null,
  def: BusinessSkillDefinition
): boolean {
  const normalizedId = normalizeId(id);
  const normalizedName = normalizeId(name);
  const ids = new Set([def.id, ...(def.aliases ?? [])].map(normalizeId));
  const prefixes = (def.prefixes ?? []).map(normalizeId);
  const names = (def.nameIncludes ?? []).map(normalizeId);
  return (
    ids.has(normalizedId) ||
    prefixes.some((prefix) => normalizedId.startsWith(prefix)) ||
    names.some((needle) => normalizedName.includes(needle))
  );
}

function isCapabilityReady(item: CapabilityInstallItem): boolean {
  return item.enabled && READY_CAPABILITY_STATUSES.has(String(item.status));
}

export function SkillMatrix() {
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [installedCapabilities, setInstalledCapabilities] = useState<CapabilityInstallItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [queryLoading, setQueryLoading] = useState(false);
  const [queryResult, setQueryResult] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [executing, setExecuting] = useState<{ skillId: string; cap: string } | null>(null);
  const [lastResultBySkill, setLastResultBySkill] = useState<Record<string, { text: string; status: "success" | "error" }>>({});
  const [lastChainSteps, setLastChainSteps] = useState<ChainStep[]>([]);
  const [syncing, setSyncing] = useState(false);
  const [uninstallTarget, setUninstallTarget] = useState<{ skill: SkillInfo } | null>(null);
  const [settingsTarget, setSettingsTarget] = useState<{ skill: SkillInfo } | null>(null);
  const [activeTab, setActiveTab] = useState<"skills" | "mcps" | "hidden" | "recycle">("skills");
  const [recycleItems, setRecycleItems] = useState<RecycleBinItem[]>([]);
  const [recycleLoading, setRecycleLoading] = useState(false);
  const [recycleError, setRecycleError] = useState<string | null>(null);
  const [mcps, setMcps] = useState<L3McpInfo[]>([]);
  const [mcpsLoading, setMcpsLoading] = useState(false);
  const [hiddenSkills, setHiddenSkills] = useState<string[]>([]);
  const [hiddenMcps, setHiddenMcps] = useState<string[]>([]);
  const [hiddenLoading, setHiddenLoading] = useState(false);
  const [toast, setToast] = useState<{ message: string; type: "success" | "error" } | null>(null);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 3000);
    return () => clearTimeout(t);
  }, [toast]);
  const [streamProgress, setStreamProgress] = useState<{
    skillId: string;
    skillName: string;
    stream: AsyncGenerator<import("../../lib/api").SkillStreamEvent>;
  } | null>(null);

  const load = useCallback(async () => {
    try {
      const [list, inventory] = await Promise.all([
        listSkills(),
        invoke<CapabilityInstallItem[]>("capability_install_local_inventory").catch(() => []),
      ]);
      setSkills(list);
      setInstalledCapabilities(inventory ?? []);
    } catch (e) {
      console.error("Failed to load skills:", e);
    } finally {
      setLoading(false);
    }
  }, []);

  const loadRecycleBin = useCallback(async () => {
    setRecycleLoading(true);
    try {
      const list = await listRecycleBinSkills();
      setRecycleItems(list);
    } catch (e) {
      console.error("Failed to load recycle bin:", e);
    } finally {
      setRecycleLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, [load]);

  const loadMcps = useCallback(async () => {
    setMcpsLoading(true);
    try {
      const list = await listL3Mcps();
      setMcps(list);
    } catch (e) {
      console.error("Failed to load MCPs:", e);
      setMcps([]);
    } finally {
      setMcpsLoading(false);
    }
  }, []);

  const loadHidden = useCallback(async () => {
    setHiddenLoading(true);
    try {
      const [skills, mcpsList] = await Promise.all([listHiddenSkills(), listHiddenMcps()]);
      setHiddenSkills(skills);
      setHiddenMcps(mcpsList);
    } catch (e) {
      console.error("Failed to load hidden:", e);
      setHiddenSkills([]);
      setHiddenMcps([]);
    } finally {
      setHiddenLoading(false);
    }
  }, []);

  useEffect(() => {
    if (activeTab === "recycle") void loadRecycleBin();
    if (activeTab === "mcps") void loadMcps();
    if (activeTab === "hidden") void loadHidden();
  }, [activeTab, loadRecycleBin, loadMcps, loadHidden]);

  // L2 云边同步：收到 INVENTORY_UPDATED 时立即刷新技能列表，平滑展示新技能
  useEffect(() => {
    const handler = () => void load();
    window.addEventListener(INVENTORY_UPDATED_EVENT, handler);
    return () => window.removeEventListener(INVENTORY_UPDATED_EVENT, handler);
  }, [load]);

  const handleSync = useCallback(async () => {
    if (syncing) return;
    setSyncing(true);
    try {
      await invoke("perform_startup_sync", { baseUrl: BACKEND_URL });
      await load();
    } catch (e) {
      console.error("[SkillMatrix] 同步失败:", e);
    } finally {
      setSyncing(false);
    }
  }, [syncing, load]);

  const handleInvoke = async () => {
    const q = query.trim();
    if (!q || queryLoading) return;
    setQueryLoading(true);
    setQueryResult(null);
    setLastChainSteps([
      { id: "1", label: "用户输入", type: "input" },
      { id: "2", label: `编排执行: 「${q.length > 18 ? q.slice(0, 18) + "…" : q}」`, type: "intent" },
      { id: "3", label: "执行中…", type: "skill" },
    ]);
    try {
      const res = await invokePlugin(q);
      const meta = res.metadata as Record<string, unknown> | undefined;
      const resultText =
        res.error_message ||
        (meta?.result != null ? String(meta.result) : "已执行");
      setQueryResult(resultText);
      const isSuccess = !res.error_message;
      const rawChain = Array.isArray(meta?.chain) ? meta.chain : null;
      const chainSteps: ChainStep[] = rawChain
        ? (rawChain as Array<{ id?: string; label?: string; type?: string }>).map((s, i) => ({
            id: s.id ?? String(i + 1),
            label: s.label ?? "—",
            type: (s.type as ChainStep["type"]) ?? undefined,
          }))
        : [
            { id: "1", label: "用户输入", type: "input" },
            { id: "2", label: `编排: 「${q.length > 18 ? q.slice(0, 18) + "…" : q}」`, type: "intent" },
            { id: "3", label: isSuccess ? "完成" : "结束", type: "done" },
          ];
      setLastChainSteps(chainSteps);
    } catch (e: unknown) {
      setQueryResult("调用失败: " + (e instanceof Error ? e.message : String(e)));
      setLastChainSteps([
        { id: "1", label: "用户输入", type: "input" },
        { id: "2", label: `编排: 「${q.length > 18 ? q.slice(0, 18) + "…" : q}」`, type: "intent" },
        { id: "3", label: "失败", type: "done" },
      ]);
    } finally {
      setQueryLoading(false);
    }
  };

  const handleUninstall = (skill: SkillInfo) => {
    setUninstallTarget({ skill });
  };

  const handleSettings = (skill: SkillInfo) => {
    setSettingsTarget({ skill });
  };

  const handleHide = async (skill: SkillInfo) => {
    const itemId = skill.item_id ?? skill.skill_id.replace(/^jpp:/, "");
    try {
      const res = await hideSkill(itemId);
      if (res.ok) {
        setToast({ message: "技能已隐藏", type: "success" });
        await load();
        await loadHidden();
      } else {
        setToast({ message: res.error ?? "隐藏失败", type: "error" });
      }
    } catch (e) {
      setToast({ message: e instanceof Error ? e.message : "隐藏失败", type: "error" });
    }
  };

  const handleUnhideSkill = async (itemId: string) => {
    try {
      const res = await unhideSkill(itemId);
      if (res.ok) {
        setToast({ message: "已取消隐藏", type: "success" });
        await load();
        await loadHidden();
      } else {
        setToast({ message: res.error ?? "取消隐藏失败", type: "error" });
      }
    } catch (e) {
      setToast({ message: e instanceof Error ? e.message : "取消隐藏失败", type: "error" });
    }
  };

  const handleHideMcp = async (itemId: string) => {
    try {
      const res = await hideMcp(itemId);
      if (res.ok) {
        setToast({ message: "MCP 已隐藏", type: "success" });
        await loadMcps();
        await loadHidden();
      } else {
        setToast({ message: res.error ?? "隐藏失败", type: "error" });
      }
    } catch (e) {
      setToast({ message: e instanceof Error ? e.message : "隐藏失败", type: "error" });
    }
  };

  const handleUnhideMcp = async (itemId: string) => {
    try {
      const res = await unhideMcp(itemId);
      if (res.ok) {
        setToast({ message: "已取消隐藏", type: "success" });
        await loadMcps();
        await loadHidden();
      } else {
        setToast({ message: res.error ?? "取消隐藏失败", type: "error" });
      }
    } catch (e) {
      setToast({ message: e instanceof Error ? e.message : "取消隐藏失败", type: "error" });
    }
  };

  const handleDeleteMcp = async (itemId: string) => {
    if (!confirm(`确定删除 MCP「${itemId}」？`)) return;
    try {
      const res = await deleteMcp(itemId);
      if (res.ok) {
        setToast({ message: "MCP 已删除", type: "success" });
        await loadMcps();
      } else {
        setToast({ message: res.error ?? "删除失败", type: "error" });
      }
    } catch (e) {
      setToast({ message: e instanceof Error ? e.message : "删除失败", type: "error" });
    }
  };

  const handleUninstallConfirm = async (purgeData: boolean) => {
    if (!uninstallTarget) return;
    const itemId = uninstallTarget.skill.item_id ?? uninstallTarget.skill.skill_id.replace(/^jpp:/, "");
    const res = await uninstallSkill(itemId, purgeData);
    if (!res.ok) throw new Error(res.error ?? "移入回收站失败");
    setUninstallTarget(null);
    await load();
    if (activeTab === "recycle") await loadRecycleBin();
  };

  const handleRestore = async (recycleId: string) => {
    setRecycleError(null);
    const res = await restoreRecycleBinSkill(recycleId);
    if (!res.ok) {
      setRecycleError(res.error ?? "恢复失败");
      return;
    }
    await load();
    await loadRecycleBin();
  };

  const handlePermanentDelete = async (recycleId: string) => {
    if (!window.confirm("确定彻底删除？此操作不可恢复。")) return;
    setRecycleError(null);
    const res = await permanentDeleteRecycleBinSkill(recycleId);
    if (!res.ok) {
      setRecycleError(res.error ?? "删除失败");
      return;
    }
    await loadRecycleBin();
  };

  const handleExecute = async (skillId: string, capabilityName: string) => {
    setExecuting({ skillId, cap: capabilityName });
    setLastResultBySkill((prev) => ({ ...prev, [skillId]: { text: "", status: "success" } }));

    if (isHrSkill(skillId)) {
      const hrInput = { target_dir: "data/hr_resumes", target_role: "backend_engineer" };
      try {
        const stream = executeSkillStream(skillId, capabilityName, hrInput);
        const skill = skills.find((s) => s.skill_id === skillId);
        setStreamProgress({
          skillId,
          skillName: skill?.name ?? "HR 透析镜",
          stream,
        });
      } catch (e: unknown) {
        const errMsg = e instanceof Error ? e.message : String(e);
        console.warn("[SkillMatrix] 流式接口不可用，回退普通执行:", errMsg);
        setExecuting(null);
        try {
          const res = await executeSkill(skillId, capabilityName, hrInput);
          const baseText =
            res.error != null
              ? res.error
              : res.result != null
                ? (typeof res.result === "object" && res.result !== null && "text" in res.result
                    ? String((res.result as { text?: string }).text ?? JSON.stringify(res.result, null, 2))
                    : JSON.stringify(res.result, null, 2))
                : res.success
                  ? "执行成功"
                  : "无返回";
          const text =
            res.error != null && res.wasm_details
              ? `${baseText}\n\n--- WASM 详情 ---\n${res.wasm_details}`
              : baseText;
          setLastResultBySkill((prev) => ({
            ...prev,
            [skillId]: { text: baseText ? `${text}\n\n(流式不可用，已用普通模式完成)` : text, status: res.error != null ? "error" : "success" },
          }));
        } catch (fallbackErr: unknown) {
          setLastResultBySkill((prev) => ({
            ...prev,
            [skillId]: {
              text: `流式失败: ${errMsg}\n回退执行也失败: ${fallbackErr instanceof Error ? fallbackErr.message : String(fallbackErr)}`,
              status: "error",
            },
          }));
        } finally {
          setExecuting(null);
        }
      }
      return;
    }

    try {
      const res = await executeSkill(skillId, capabilityName, {});
      const baseText =
        res.error != null
          ? res.error
          : res.result != null
            ? (typeof res.result === "object" && res.result !== null && "text" in res.result
                ? String((res.result as { text?: string }).text ?? JSON.stringify(res.result, null, 2))
                : JSON.stringify(res.result, null, 2))
            : res.success
              ? "执行成功"
              : "无返回";
      const text =
        res.error != null && res.wasm_details
          ? `${baseText}\n\n--- WASM 详情 ---\n${res.wasm_details}`
          : baseText;
      const status = res.error != null ? "error" : "success";
      setLastResultBySkill((prev) => ({ ...prev, [skillId]: { text, status } }));
    } catch (e: unknown) {
      setLastResultBySkill((prev) => ({
        ...prev,
        [skillId]: { text: "执行失败: " + (e instanceof Error ? e.message : String(e)), status: "error" },
      }));
    } finally {
      setExecuting(null);
    }
  };

  const handleStreamProgressClose = (err?: string) => {
    if (streamProgress) {
      setLastResultBySkill((prev) => ({
        ...prev,
        [streamProgress.skillId]: {
          text: err
            ? `流式执行失败: ${err}`
            : "⚡ 批量分析完成！报告已保存至 data/hr_analysis/ 目录。",
          status: err ? ("error" as const) : ("success" as const),
        },
      }));
      setStreamProgress(null);
    }
    setExecuting(null);
  };

  const businessSkills = BUSINESS_SKILLS
    .filter((def) => {
      if (DEV_RUNTIME) return true;
      const installedByRegistry = installedCapabilities.some(
        (item) => isCapabilityReady(item) && matchesBusinessDefinition(item.id, item.name, def)
      );
      const installedByApi = skills.some((skill) => matchesBusinessDefinition(skill.item_id ?? skill.skill_id, skill.name, def));
      return installedByRegistry || installedByApi;
    })
    .map((def): SkillInfo => {
      const realSkill = skills.find((skill) => matchesBusinessDefinition(skill.item_id ?? skill.skill_id, skill.name, def));
      return {
        skill_id: realSkill?.skill_id ?? def.id,
        item_id: realSkill?.item_id ?? def.id,
        name: def.name,
        version: realSkill?.version ?? "1.0.0",
        description: def.description,
        status: realSkill?.status ?? "installed",
        capabilities: realSkill?.capabilities ?? [],
        permissions: realSkill?.permissions ?? [],
        execution_count: realSkill?.execution_count,
        last_executed_at: realSkill?.last_executed_at,
      };
    });

  const otherSkills = skills.filter(
    (skill) => !BUSINESS_SKILLS.some((def) => matchesBusinessDefinition(skill.item_id ?? skill.skill_id, skill.name, def))
  );
  const visibleSkills = [...businessSkills, ...otherSkills];

  return (
    <div className="h-full flex flex-col p-6 overflow-auto">
      <header className="flex-shrink-0 mb-6 flex items-start justify-between gap-4">
        <div>
          <h1
            className="font-sci-fi text-2xl font-bold text-transparent bg-clip-text bg-gradient-to-r from-rose-400 to-rose-600"
            style={{ fontFamily: "Orbitron, sans-serif" }}
          >
            Skill Matrix
          </h1>
          <p className="text-slate-500 text-sm mt-0.5">插件与权限 · 军械库</p>
        </div>
        <button
          type="button"
          onClick={handleSync}
          disabled={syncing}
          className="flex items-center gap-2 px-3 py-2 rounded-lg bg-slate-700/80 hover:bg-slate-600/80 disabled:opacity-50 text-slate-300 hover:text-white text-sm font-mono transition-colors"
          title="从 L2 拉取最新技能"
        >
          {syncing ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <RefreshCw className="w-4 h-4" />
          )}
          立即同步
        </button>
      </header>

      <motion.section
        className="flex-shrink-0 glass-panel rounded-xl p-4 mb-6"
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        <h2 className="font-mono text-xs uppercase tracking-wider text-slate-500 mb-3">自然语言执行</h2>
        <div className="flex gap-2">
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleInvoke()}
            placeholder="例如：列出桌面文件"
            className="flex-1 px-4 py-2 rounded-lg bg-white/5 border border-white/10 text-white placeholder-slate-500 focus:outline-none focus:border-rose-500/50 font-mono text-sm"
          />
          <button
            type="button"
            onClick={handleInvoke}
            disabled={!query.trim() || queryLoading}
            className="px-4 py-2 rounded-lg bg-rose-600 hover:bg-rose-500 disabled:opacity-50 flex items-center gap-2 font-mono text-sm"
          >
            {queryLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
            执行
          </button>
        </div>
        {queryResult != null && (
          <div className="mt-3 p-3 rounded-lg bg-black/40 text-xs overflow-x-auto max-h-[70vh] overflow-y-auto border border-white/5 custom-scrollbar">
            <MarkdownMessage content={queryResult} />
          </div>
        )}
      </motion.section>

      <motion.section
        className="flex-shrink-0 mb-6"
        initial={{ opacity: 0, y: 4 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3, delay: 0.05 }}
      >
        <SkillChainView steps={lastChainSteps} />
      </motion.section>

      <section className="flex-1 min-h-0">
        <div className="flex items-center gap-4 mb-3">
          <div className="flex gap-1 p-1 rounded-lg bg-white/5 border border-white/10">
            <button
              type="button"
              onClick={() => setActiveTab("skills")}
              className={`px-3 py-1.5 rounded-md text-sm font-mono transition-colors ${
                activeTab === "skills"
                  ? "bg-rose-500/30 text-rose-400"
                  : "text-slate-400 hover:text-white"
              }`}
            >
              已安装技能
            </button>
            <button
              type="button"
              onClick={() => setActiveTab("mcps")}
              className={`px-3 py-1.5 rounded-md text-sm font-mono transition-colors flex items-center gap-1.5 ${
                activeTab === "mcps"
                  ? "bg-violet-500/30 text-violet-400"
                  : "text-slate-400 hover:text-white"
              }`}
            >
              <Plug className="w-3.5 h-3.5" />
              MCP {mcps.length > 0 && `(${mcps.length})`}
            </button>
            <button
              type="button"
              onClick={() => setActiveTab("hidden")}
              className={`px-3 py-1.5 rounded-md text-sm font-mono transition-colors flex items-center gap-1.5 ${
                activeTab === "hidden"
                  ? "bg-amber-500/30 text-amber-400"
                  : "text-slate-400 hover:text-white"
              }`}
            >
              <EyeOff className="w-3.5 h-3.5" />
              已隐藏 {(hiddenSkills.length + hiddenMcps.length) > 0 && `(${hiddenSkills.length + hiddenMcps.length})`}
            </button>
            <button
              type="button"
              onClick={() => setActiveTab("recycle")}
              className={`px-3 py-1.5 rounded-md text-sm font-mono transition-colors flex items-center gap-1.5 ${
                activeTab === "recycle"
                  ? "bg-rose-500/30 text-rose-400"
                  : "text-slate-400 hover:text-white"
              }`}
            >
              <Trash2 className="w-3.5 h-3.5" />
              回收站 {recycleItems.length > 0 && `(${recycleItems.length})`}
            </button>
          </div>
        </div>

        <AnimatePresence>
          {toast && (
            <motion.div
              initial={{ opacity: 0, y: -10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              className={`fixed top-20 left-1/2 -translate-x-1/2 z-[100] px-4 py-2 rounded-lg text-sm ${
                toast.type === "success" ? "bg-emerald-500/90 text-white" : "bg-rose-500/90 text-white"
              }`}
              onClick={() => setToast(null)}
            >
              {toast.message}
            </motion.div>
          )}
        </AnimatePresence>

        {activeTab === "skills" && (
          <>
            {loading ? (
          <div className="flex items-center gap-2 text-slate-400 py-12">
            <Loader2 className="w-5 h-5 animate-spin" />
            加载中...
          </div>
            ) : visibleSkills.length === 0 ? (
              <p className="text-slate-400 py-12 font-mono text-sm">暂无技能，请确保后端已启动并注册技能。</p>
            ) : (
              <div className="space-y-6">
                <div>
                  <div className="mb-3 flex items-end justify-between gap-3">
                    <div>
                      <h2 className="text-sm font-semibold text-cyan-100">业务 Skill</h2>
                      <p className="mt-1 text-xs text-slate-500">
                        每个业务域只保留一个产品级入口；MCP、模型和子工具作为依赖安装。
                      </p>
                    </div>
                    <span className="font-mono text-xs text-slate-500">{businessSkills.length}/{BUSINESS_SKILLS.length}</span>
                  </div>
                  {businessSkills.length === 0 ? (
                    <p className="rounded-xl border border-white/10 bg-white/5 px-4 py-6 text-sm text-slate-500">
                      当前还没有安装业务 Skill。请先到能力安装页订阅 BI、PMO、招聘、桌面执行、游戏 QA 或英语助手。
                    </p>
                  ) : (
                    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-3 gap-4">
                      {businessSkills.map((skill) => {
                        const last = lastResultBySkill[skill.skill_id];
                        return (
                          <LiveTile
                            key={skill.skill_id}
                            skill={skill}
                            lastResult={last?.text ?? null}
                            lastStatus={last?.status ?? "idle"}
                            isExecuting={executing?.skillId === skill.skill_id}
                            onExecute={(capName) => handleExecute(skill.skill_id, capName)}
                            onExpand={() => setExpandedId(skill.skill_id)}
                            onSettings={() => handleSettings(skill)}
                            onHide={() => handleHide(skill)}
                            onUninstall={() => handleUninstall(skill)}
                            permissions={skill.permissions}
                            liveStatus={
                              skill.execution_count != null && skill.execution_count > 0
                                ? `已执行 ${skill.execution_count} 次${skill.last_executed_at ? ` · 上次 ${skill.last_executed_at}` : ""}`
                                : skill.description
                            }
                          />
                        );
                      })}
                    </div>
                  )}
                </div>

                {otherSkills.length > 0 && (
                  <div>
                    <div className="mb-3">
                      <h2 className="text-sm font-semibold text-slate-300">其他技术技能</h2>
                      <p className="mt-1 text-xs text-slate-500">
                        开发、测试或底层能力入口，不作为业务 Skill 主入口。
                      </p>
                    </div>
                    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
                      {otherSkills.map((skill) => {
                        const last = lastResultBySkill[skill.skill_id];
                        return (
                          <LiveTile
                            key={skill.skill_id}
                            skill={skill}
                            lastResult={last?.text ?? null}
                            lastStatus={last?.status ?? "idle"}
                            isExecuting={executing?.skillId === skill.skill_id}
                            onExecute={(capName) => handleExecute(skill.skill_id, capName)}
                            onExpand={() => setExpandedId(skill.skill_id)}
                            onSettings={() => handleSettings(skill)}
                            onHide={() => handleHide(skill)}
                            onUninstall={() => handleUninstall(skill)}
                            permissions={skill.permissions}
                            liveStatus={
                              skill.execution_count != null && skill.execution_count > 0
                                ? `已执行 ${skill.execution_count} 次${skill.last_executed_at ? ` · 上次 ${skill.last_executed_at}` : ""}`
                                : null
                            }
                          />
                        );
                      })}
                    </div>
                  </div>
                )}
              </div>
            )}
          </>
        )}

        {activeTab === "mcps" && (
          <>
            {mcpsLoading ? (
              <div className="flex items-center gap-2 text-slate-400 py-12">
                <Loader2 className="w-5 h-5 animate-spin" />
                加载中...
              </div>
            ) : mcps.length === 0 ? (
              <p className="text-slate-400 py-12 font-mono text-sm">暂无 L3 MCP</p>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
                {mcps.map((mcp) => (
                  <motion.div
                    key={mcp.item_id}
                    initial={{ opacity: 0, scale: 0.96 }}
                    animate={{ opacity: 1, scale: 1 }}
                    className="glass-panel rounded-xl p-4 border border-white/10 flex flex-col gap-3"
                  >
                    <div className="flex items-center gap-2">
                      <Plug className="w-4 h-4 text-violet-400 flex-shrink-0" />
                      <span className="font-mono text-sm font-medium text-white truncate">{mcp.name}</span>
                    </div>
                    <p className="text-[11px] text-slate-500 font-mono truncate">{mcp.item_id}</p>
                    <div className="flex gap-2 mt-auto">
                      <button
                        type="button"
                        onClick={() => handleHideMcp(mcp.item_id)}
                        className="flex-1 flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg bg-amber-600/80 hover:bg-amber-500 text-white text-xs font-mono"
                      >
                        <EyeOff className="w-3.5 h-3.5" />
                        隐藏
                      </button>
                      <button
                        type="button"
                        onClick={() => handleDeleteMcp(mcp.item_id)}
                        className="flex-1 flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg bg-rose-600/80 hover:bg-rose-500 text-white text-xs font-mono"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                        删除
                      </button>
                    </div>
                  </motion.div>
                ))}
              </div>
            )}
          </>
        )}

        {activeTab === "hidden" && (
          <>
            {hiddenLoading ? (
              <div className="flex items-center gap-2 text-slate-400 py-12">
                <Loader2 className="w-5 h-5 animate-spin" />
                加载中...
              </div>
            ) : hiddenSkills.length === 0 && hiddenMcps.length === 0 ? (
              <p className="text-slate-400 py-12 font-mono text-sm">暂无已隐藏项</p>
            ) : (
              <div className="space-y-4">
                {hiddenSkills.length > 0 && (
                  <div>
                    <h3 className="text-sm font-mono text-slate-400 mb-2">已隐藏技能</h3>
                    <div className="flex flex-wrap gap-2">
                      {hiddenSkills.map((itemId) => (
                        <div
                          key={itemId}
                          className="flex items-center gap-2 px-3 py-2 rounded-lg bg-white/5 border border-white/10"
                        >
                          <span className="font-mono text-sm text-white">{itemId}</span>
                          <button
                            type="button"
                            onClick={() => handleUnhideSkill(itemId)}
                            className="px-2 py-1 rounded bg-cyan-600/80 hover:bg-cyan-500 text-white text-xs"
                          >
                            取消隐藏
                          </button>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
                {hiddenMcps.length > 0 && (
                  <div>
                    <h3 className="text-sm font-mono text-slate-400 mb-2">已隐藏 MCP</h3>
                    <div className="flex flex-wrap gap-2">
                      {hiddenMcps.map((itemId) => (
                        <div
                          key={itemId}
                          className="flex items-center gap-2 px-3 py-2 rounded-lg bg-white/5 border border-white/10"
                        >
                          <span className="font-mono text-sm text-white">{itemId}</span>
                          <button
                            type="button"
                            onClick={() => handleUnhideMcp(itemId)}
                            className="px-2 py-1 rounded bg-cyan-600/80 hover:bg-cyan-500 text-white text-xs"
                          >
                            取消隐藏
                          </button>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </>
        )}

        {activeTab === "recycle" && (
          <>
            {recycleError && (
              <p className="text-amber-400 text-sm mb-3 p-2 rounded-lg bg-amber-500/10 border border-amber-500/30">
                {recycleError}
              </p>
            )}
            {recycleLoading ? (
              <div className="flex items-center gap-2 text-slate-400 py-12">
                <Loader2 className="w-5 h-5 animate-spin" />
                加载中...
              </div>
            ) : recycleItems.length === 0 ? (
              <p className="text-slate-400 py-12 font-mono text-sm">回收站为空</p>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
                {recycleItems.map((item) => (
                  <motion.div
                    key={item.recycle_id}
                    initial={{ opacity: 0, scale: 0.96 }}
                    animate={{ opacity: 1, scale: 1 }}
                    className="glass-panel rounded-xl p-4 border border-white/10 flex flex-col gap-3"
                  >
                    <div className="flex items-center gap-2">
                      <Trash2 className="w-4 h-4 text-slate-500 flex-shrink-0" />
                      <span className="font-mono text-sm font-medium text-white truncate">{item.name}</span>
                    </div>
                    <p className="text-[11px] text-slate-500 font-mono">
                      {item.item_id} · {item.source} · {item.deleted_at?.slice(0, 10) || ""}
                    </p>
                    <div className="flex gap-2 mt-auto">
                      <button
                        type="button"
                        onClick={() => handleRestore(item.recycle_id)}
                        className="flex-1 flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg bg-cyan-600/80 hover:bg-cyan-500 text-white text-xs font-mono"
                      >
                        <RotateCcw className="w-3.5 h-3.5" />
                        恢复
                      </button>
                      <button
                        type="button"
                        onClick={() => handlePermanentDelete(item.recycle_id)}
                        className="flex-1 flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg bg-rose-600/80 hover:bg-rose-500 text-white text-xs font-mono"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                        彻底删除
                      </button>
                    </div>
                  </motion.div>
                ))}
              </div>
            )}
          </>
        )}
      </section>

      {expandedId && (() => {
        const detailSkill = visibleSkills.find((s) => s.skill_id === expandedId);
        return detailSkill ? (
          <SkillDetailModal
            skill={detailSkill}
            onClose={() => setExpandedId(null)}
            onExecute={handleExecute}
            onHide={() => {
              setExpandedId(null);
              handleHide(detailSkill);
            }}
            onUninstall={() => {
              setExpandedId(null);
              setUninstallTarget({ skill: detailSkill });
            }}
            executing={executing}
            lastResult={lastResultBySkill[expandedId]?.text ?? null}
          />
        ) : null;
      })()}

      {settingsTarget && (
        <SkillSettingsDrawer
          skillId={settingsTarget.skill.skill_id}
          skillName={settingsTarget.skill.name}
          onClose={() => setSettingsTarget(null)}
        />
      )}

      {uninstallTarget && (
        <UninstallSkillModal
          skillName={uninstallTarget.skill.name}
          itemId={uninstallTarget.skill.item_id ?? uninstallTarget.skill.skill_id.replace(/^jpp:/, "")}
          onConfirm={handleUninstallConfirm}
          onClose={() => setUninstallTarget(null)}
        />
      )}

      {streamProgress && (
        <BatchProgressBar
          visible={!!streamProgress}
          skillId={streamProgress.skillId}
          skillName={streamProgress.skillName}
          stream={streamProgress.stream}
          onClose={handleStreamProgressClose}
        />
      )}
    </div>
  );
}
