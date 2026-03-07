/**
 * Skill Matrix - 军械库：网格磁贴 + 自然语言执行 + 悬停 Permission X-Ray
 */

import { useState, useEffect, useCallback } from "react";
import { motion } from "framer-motion";
import { Play, Loader2, RefreshCw } from "lucide-react";
import { invoke } from "@tauri-apps/api/core";
import { listSkills, executeSkill, invokePlugin, SkillInfo, BACKEND_URL } from "../../lib/api";
import { INVENTORY_UPDATED_EVENT } from "../../hooks/useUISyncEventSource";
import { LiveTile } from "../components/LiveTile";
import { SkillDetailModal } from "../components/SkillDetailModal";
import { SkillChainView, type ChainStep } from "../components/SkillChainView";

export function SkillMatrix() {
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [queryLoading, setQueryLoading] = useState(false);
  const [queryResult, setQueryResult] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [executing, setExecuting] = useState<{ skillId: string; cap: string } | null>(null);
  const [lastResultBySkill, setLastResultBySkill] = useState<Record<string, { text: string; status: "success" | "error" }>>({});
  const [lastChainSteps, setLastChainSteps] = useState<ChainStep[]>([]);
  const [syncing, setSyncing] = useState(false);

  const load = useCallback(async () => {
    try {
      const list = await listSkills();
      setSkills(list);
    } catch (e) {
      console.error("Failed to load skills:", e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, [load]);

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

  const handleExecute = async (skillId: string, capabilityName: string) => {
    setExecuting({ skillId, cap: capabilityName });
    setLastResultBySkill((prev) => ({ ...prev, [skillId]: { text: "", status: "success" } }));
    try {
      const res = await executeSkill(skillId, capabilityName, {});
      const baseText =
        res.error != null
          ? res.error
          : res.result != null
            ? JSON.stringify(res.result, null, 2)
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
          <pre className="mt-3 p-3 rounded-lg bg-black/40 text-xs overflow-x-auto max-h-28 overflow-y-auto border border-white/5 font-mono custom-scrollbar">
            {queryResult}
          </pre>
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
        <h2 className="font-mono text-xs uppercase tracking-wider text-slate-500 mb-3">已安装技能</h2>
        {loading ? (
          <div className="flex items-center gap-2 text-slate-400 py-12">
            <Loader2 className="w-5 h-5 animate-spin" />
            加载中...
          </div>
        ) : skills.length === 0 ? (
          <p className="text-slate-400 py-12 font-mono text-sm">暂无技能，请确保后端已启动并注册技能。</p>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {skills.map((skill) => {
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
        )}
      </section>

      {expandedId && (() => {
        const detailSkill = skills.find((s) => s.skill_id === expandedId);
        return detailSkill ? (
          <SkillDetailModal
            skill={detailSkill}
            onClose={() => setExpandedId(null)}
            onExecute={handleExecute}
            executing={executing}
            lastResult={lastResultBySkill[expandedId]?.text ?? null}
          />
        ) : null;
      })()}
    </div>
  );
}
