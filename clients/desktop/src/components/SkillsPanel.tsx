/**
 * 技能面板 - 列出已注册技能，支持自然语言调用与直接执行能力
 */

import { useState, useEffect, useCallback } from "react";
import { Zap, Play, Loader2, ChevronDown, ChevronRight } from "lucide-react";
import { listSkills, executeSkill, invokePlugin, SkillInfo } from "../lib/api";
import { MarkdownMessage } from "./Chat/MarkdownMessage";
import { INVENTORY_UPDATED_EVENT } from "../hooks/useUISyncEventSource";

export default function SkillsPanel() {
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [orchestratorInput, setOrchestratorInput] = useState("");
  const [orchestratorLoading, setOrchestratorLoading] = useState(false);
  const [orchestratorResult, setOrchestratorResult] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [executing, setExecuting] = useState<{ skillId: string; cap: string } | null>(null);
  const [execResult, setExecResult] = useState<string | null>(null);

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

  // L2 云边同步：收到 INVENTORY_UPDATED 时立即刷新技能列表
  useEffect(() => {
    const handler = () => void load();
    window.addEventListener(INVENTORY_UPDATED_EVENT, handler);
    return () => window.removeEventListener(INVENTORY_UPDATED_EVENT, handler);
  }, [load]);

  const handleOrchestratorInvoke = async () => {
    const query = orchestratorInput.trim();
    if (!query || orchestratorLoading) return;
    setOrchestratorLoading(true);
    setOrchestratorResult(null);
    try {
      const res = await invokePlugin(query);
      const text =
        res.error_message ||
        (res.metadata?.result ? String(res.metadata.result) : "已执行");
      setOrchestratorResult(text);
    } catch (e: unknown) {
      setOrchestratorResult("调用失败: " + (e instanceof Error ? e.message : String(e)));
    } finally {
      setOrchestratorLoading(false);
    }
  };

  const handleExecuteCapability = async (
    skillId: string,
    capabilityName: string
  ) => {
    setExecuting({ skillId, cap: capabilityName });
    setExecResult(null);
    try {
      const res = await executeSkill(skillId, capabilityName, {});
      const baseText = res.error
        ? res.error
        : res.result != null
          ? (typeof res.result === "object" && res.result !== null && "text" in res.result
              ? String((res.result as { text?: string }).text ?? JSON.stringify(res.result, null, 2))
              : JSON.stringify(res.result, null, 2))
          : res.success
            ? "执行成功"
            : "无返回";
      const text =
        res.error && res.wasm_details
          ? `${baseText}\n\n--- WASM 详情 ---\n${res.wasm_details}`
          : baseText;
      setExecResult(text);
    } catch (e: unknown) {
      setExecResult("执行失败: " + (e instanceof Error ? e.message : String(e)));
    } finally {
      setExecuting(null);
    }
  };

  return (
    <div className="h-full bg-slate-800/50 rounded-lg border border-purple-500/20 p-4 overflow-y-auto">
      <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
        <Zap className="w-5 h-5 text-amber-400" />
        技能
      </h2>

      {/* 自然语言调用编排器 */}
      <div className="mb-4">
        <label className="block text-sm text-slate-400 mb-1">自然语言执行</label>
        <div className="flex gap-2">
          <input
            type="text"
            value={orchestratorInput}
            onChange={(e) => setOrchestratorInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleOrchestratorInvoke()}
            placeholder="例如：列出桌面文件"
            className="flex-1 px-3 py-2 rounded-lg bg-slate-700/50 border border-purple-500/20 text-white placeholder-slate-500 focus:outline-none focus:border-purple-500/50"
          />
          <button
            onClick={handleOrchestratorInvoke}
            disabled={!orchestratorInput.trim() || orchestratorLoading}
            className="px-3 py-2 rounded-lg bg-purple-600 hover:bg-purple-500 disabled:opacity-50 flex items-center gap-1"
          >
            {orchestratorLoading ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Play className="w-4 h-4" />
            )}
            执行
          </button>
        </div>
        {orchestratorResult != null && (
          <pre className="mt-2 p-2 rounded bg-slate-700/50 text-xs overflow-x-auto max-h-[60vh] overflow-y-auto">
            {orchestratorResult}
          </pre>
        )}
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-8 text-slate-400">
          <Loader2 className="w-6 h-6 animate-spin mr-2" />
          加载中...
        </div>
      ) : skills.length === 0 ? (
        <div className="text-center text-slate-400 py-6">
          <Zap className="w-10 h-10 mx-auto mb-2 opacity-50" />
          <p>暂无技能</p>
          <p className="text-sm mt-1">后端注册技能后将显示在这里</p>
        </div>
      ) : (
        <div className="space-y-2">
          {skills.map((skill) => {
            const isExpanded = expandedId === skill.skill_id;
            const caps = skill.capabilities ?? [];
            return (
              <div
                key={skill.skill_id}
                className="bg-slate-700/50 rounded-lg border border-purple-500/10 overflow-hidden"
              >
                <button
                  onClick={() =>
                    setExpandedId(isExpanded ? null : skill.skill_id)
                  }
                  className="w-full flex items-center gap-2 p-3 text-left hover:bg-slate-700/70"
                >
                  {caps.length > 0 ? (
                    isExpanded ? (
                      <ChevronDown className="w-4 h-4 flex-shrink-0" />
                    ) : (
                      <ChevronRight className="w-4 h-4 flex-shrink-0" />
                    )
                  ) : (
                    <span className="w-4" />
                  )}
                  <span className="font-medium truncate">{skill.name}</span>
                  <span className="text-xs text-slate-500 flex-shrink-0">
                    v{skill.version}
                  </span>
                </button>
                {isExpanded && (
                  <div className="px-3 pb-3 pt-0 border-t border-slate-600/50">
                    {skill.description && (
                      <p className="text-sm text-slate-400 mt-2 mb-2">
                        {skill.description}
                      </p>
                    )}
                    <div className="space-y-1">
                      {caps.map((cap) => {
                        const name =
                          (cap.name as string) ||
                          (typeof cap === "string" ? cap : "");
                        if (!name) return null;
                        const key = `${skill.skill_id}:${name}`;
                        const isExec =
                          executing?.skillId === skill.skill_id &&
                          executing?.cap === name;
                        return (
                          <div
                            key={key}
                            className="flex items-center justify-between gap-2 text-sm"
                          >
                            <span className="text-slate-300 truncate">
                              {name}
                            </span>
                            <button
                              onClick={() =>
                                handleExecuteCapability(skill.skill_id, name)
                              }
                              disabled={isExec}
                              className="flex-shrink-0 px-2 py-1 rounded bg-purple-600/80 hover:bg-purple-500 text-xs disabled:opacity-50 flex items-center gap-1"
                            >
                              {isExec ? (
                                <Loader2 className="w-3 h-3 animate-spin" />
                              ) : (
                                <Play className="w-3 h-3" />
                              )}
                              执行
                            </button>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {execResult != null && (
        <div className="mt-4 p-3 rounded-lg bg-slate-700/50 border border-purple-500/20">
          <div className="text-xs text-slate-400 mb-1">上次执行结果</div>
          <div className="text-xs overflow-x-auto max-h-64 overflow-y-auto custom-scrollbar">
            <MarkdownMessage content={execResult} />
          </div>
        </div>
      )}
    </div>
  );
}
