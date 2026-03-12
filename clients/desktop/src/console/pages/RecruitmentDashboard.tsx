/**
 * RecruitmentDashboard - 一键式全链路招聘大盘
 *
 * 战术收网 + 灵魂审判，赛博朋克终端风，对用户隐藏 MCP/Wasm 底层概念
 * 配置持久化到 localStorage，下次启动自动恢复
 */

import { useState, useRef, useEffect } from "react";
import { motion } from "framer-motion";
import { Zap, Loader2 } from "lucide-react";
import { startRecruitmentTask } from "../../lib/api";

const RECRUITMENT_CONFIG_KEY = "jachin_recruitment_config";

interface RecruitmentConfig {
  jobName: string;
  maxCount: number;
  filterTab: string;
  requestResume: boolean;
  jdContent: string;
  focusKeywords: string;
  strictness: "lenient" | "standard" | "strict";
  outputDir: string;
  forceReanalyze: boolean;
}

const DEFAULT_CONFIG: RecruitmentConfig = {
  jobName: "Java_杭州 4-6K",
  maxCount: 20,
  filterTab: "全部",
  requestResume: true,
  jdContent: "",
  focusKeywords: "",
  strictness: "standard",
  outputDir: "",
  forceReanalyze: false,
};

function loadConfig(): RecruitmentConfig {
  try {
    const raw = localStorage.getItem(RECRUITMENT_CONFIG_KEY);
    if (!raw) return DEFAULT_CONFIG;
    const parsed = JSON.parse(raw) as Partial<RecruitmentConfig>;
    const merged = { ...DEFAULT_CONFIG, ...parsed };
    // 兜底：旧配置可能无 requestResume，默认 true 以启用自动求简历
    if (merged.requestResume === undefined) {
      merged.requestResume = true;
    }
    return merged;
  } catch {
    return DEFAULT_CONFIG;
  }
}

function saveConfig(config: RecruitmentConfig) {
  try {
    localStorage.setItem(RECRUITMENT_CONFIG_KEY, JSON.stringify(config));
  } catch {
    // ignore
  }
}

export function RecruitmentDashboard() {
  const [config, setConfig] = useState<RecruitmentConfig>(DEFAULT_CONFIG);
  const [running, setRunning] = useState(false);
  const [logs, setLogs] = useState<string[]>([]);
  const logEndRef = useRef<HTMLDivElement>(null);
  const jdTextareaRef = useRef<HTMLTextAreaElement>(null);

  const hasLoadedRef = useRef(false);
  useEffect(() => {
    setConfig(loadConfig());
    hasLoadedRef.current = true;
  }, []);

  useEffect(() => {
    if (!hasLoadedRef.current) return;
    const t = setTimeout(() => saveConfig(config), 500);
    return () => clearTimeout(t);
  }, [config]);

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs]);

  const updateConfig = <K extends keyof RecruitmentConfig>(key: K, value: RecruitmentConfig[K]) => {
    setConfig((prev) => ({ ...prev, [key]: value }));
  };

  const handleStart = async () => {
    const jn = config.jobName.trim();
    if (!jn) return;
    setRunning(true);
    setLogs([]);
    const requestResume = config.requestResume !== false;
    // 从 textarea ref 读取最新 JD，避免 React 状态滞后导致传入空 JD（此前会回退到默认「云边协同架构师」）
    const jdContent = (jdTextareaRef.current?.value ?? config.jdContent ?? "").trim();
    saveConfig({ ...config, jdContent });
    try {
      const stream = startRecruitmentTask({
        job_name: jn,
        max_count: config.maxCount,
        filter_tab: config.filterTab,
        request_resume: requestResume,
        output_dir: config.outputDir,
        force_reanalyze: config.forceReanalyze,
        jd_content: jdContent,
        focus_keywords: config.focusKeywords,
        strictness: config.strictness,
      });
      for await (const ev of stream) {
        if (ev.msg) setLogs((prev) => [...prev, ev.msg!]);
        if (ev.status === "progress" && ev.filename) {
          setLogs((prev) => [...prev, `  └ ${ev.filename} [已落盘]`]);
        }
      }
    } catch (e) {
      setLogs((prev) => [...prev, `⚠️ 异常: ${e instanceof Error ? e.message : String(e)}`]);
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <div className="flex-shrink-0 px-6 py-4 border-b border-cyan-500/20">
        <h1 className="text-xl font-bold text-cyan-400/95 tracking-wider">
          [RECRUITMENT.DASHBOARD] 一键式全链路招聘大盘
        </h1>
        <p className="text-xs text-slate-400 mt-1">
          战术收网 → HR 透析镜，对用户隐藏 MCP/Wasm 底层概念
        </p>
      </div>

      <div className="flex-1 overflow-auto p-6 space-y-6">
        {/* 上半部分：战术收网 */}
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          className="rounded-xl border border-cyan-500/30 bg-slate-900/60 p-5"
          style={{
            boxShadow: "0 0 20px rgba(0, 212, 255, 0.08), inset 0 0 40px rgba(0, 212, 255, 0.02)",
          }}
        >
          <div className="font-mono text-sm text-cyan-400/90 mb-4 tracking-wider">
            [1] 战术收网（岗位、数量、来源）
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div>
              <label className="block text-xs text-slate-400 mb-1">岗位名称</label>
              <input
                type="text"
                value={config.jobName}
                onChange={(e) => updateConfig("jobName", e.target.value)}
                placeholder="Java_杭州 4-6K"
                className="w-full px-3 py-2 rounded bg-slate-800/80 border border-cyan-500/30 text-cyan-100 placeholder-slate-500 focus:border-cyan-400/60 focus:ring-1 focus:ring-cyan-400/30 outline-none"
              />
            </div>
            <div>
              <label className="block text-xs text-slate-400 mb-1">最大数量</label>
              <input
                type="number"
                value={config.maxCount}
                onChange={(e) => updateConfig("maxCount", Math.max(1, parseInt(e.target.value, 10) || 20))}
                min={1}
                max={100}
                className="w-full px-3 py-2 rounded bg-slate-800/80 border border-cyan-500/30 text-cyan-100 focus:border-cyan-400/60 focus:ring-1 focus:ring-cyan-400/30 outline-none"
              />
            </div>
            <div>
              <label className="block text-xs text-slate-400 mb-1">消息来源</label>
              <select
                value={config.filterTab}
                onChange={(e) => updateConfig("filterTab", e.target.value)}
                className="w-full px-3 py-2 rounded bg-slate-800/80 border border-cyan-500/30 text-cyan-100 focus:border-cyan-400/60 outline-none"
              >
                <option value="全部">全部</option>
                <option value="新招呼">新招呼</option>
              </select>
            </div>
            <div className="md:col-span-3 flex items-center gap-3">
              <input
                type="checkbox"
                id="requestResume"
                checked={config.requestResume}
                onChange={(e) => updateConfig("requestResume", e.target.checked)}
                className="w-4 h-4 rounded border-cyan-500/50 bg-slate-800/80 text-cyan-500 focus:ring-cyan-400/50"
              />
              <label htmlFor="requestResume" className="text-sm text-slate-300 cursor-pointer">
                无简历时自动求简历（向候选人索要附件）
              </label>
            </div>
          </div>
        </motion.div>

        {/* 下半部分：灵魂审判 */}
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.05 }}
          className="rounded-xl border border-cyan-500/30 bg-slate-900/60 p-5"
          style={{
            boxShadow: "0 0 20px rgba(0, 212, 255, 0.08), inset 0 0 40px rgba(0, 212, 255, 0.02)",
          }}
        >
          <div className="font-mono text-sm text-cyan-400/90 mb-4 tracking-wider">
            [2] 灵魂审判（JD、考察项、严厉度）
          </div>
          <div className="space-y-4">
            <div>
              <label className="block text-xs text-slate-400 mb-1">岗位 JD（留空将用岗位名兜底，建议填写完整 JD 以获得精准分析）</label>
              <textarea
                ref={jdTextareaRef}
                value={config.jdContent}
                onChange={(e) => updateConfig("jdContent", e.target.value)}
                placeholder="云边协同后端架构师：精通 Rust/Go..."
                rows={3}
                className="w-full px-3 py-2 rounded bg-slate-800/80 border border-cyan-500/30 text-cyan-100 placeholder-slate-500 focus:border-cyan-400/60 focus:ring-1 focus:ring-cyan-400/30 outline-none resize-none"
              />
            </div>
            <div>
              <label className="block text-xs text-slate-400 mb-1">重点考察项</label>
              <input
                type="text"
                value={config.focusKeywords}
                onChange={(e) => updateConfig("focusKeywords", e.target.value)}
                placeholder="必须精通 K8s 部署，了解微服务架构"
                className="w-full px-3 py-2 rounded bg-slate-800/80 border border-cyan-500/30 text-cyan-100 placeholder-slate-500 focus:border-cyan-400/60 focus:ring-1 focus:ring-cyan-400/30 outline-none"
              />
            </div>
            <div>
              <label className="block text-xs text-slate-400 mb-1">评判严厉度</label>
              <select
                value={config.strictness}
                onChange={(e) => updateConfig("strictness", e.target.value as "lenient" | "standard" | "strict")}
                className="w-full px-3 py-2 rounded bg-slate-800/80 border border-cyan-500/30 text-cyan-100 focus:border-cyan-400/60 outline-none"
              >
                <option value="lenient">宽容（伯乐眼光）</option>
                <option value="standard">标准（客观理性）</option>
                <option value="strict">极度严苛（宁缺毋滥）</option>
              </select>
            </div>
            <div>
              <label className="block text-xs text-slate-400 mb-1">输出目录</label>
              <input
                type="text"
                value={config.outputDir}
                onChange={(e) => updateConfig("outputDir", e.target.value)}
                placeholder="留空则默认保存至 data/hr_analysis/岗位名"
                className="w-full px-3 py-2 rounded bg-slate-800/80 border border-cyan-500/30 text-cyan-100 placeholder-slate-500 focus:border-cyan-400/60 focus:ring-1 focus:ring-cyan-400/30 outline-none"
              />
            </div>
            <div className="flex items-center gap-3">
              <input
                type="checkbox"
                id="forceReanalyze"
                checked={config.forceReanalyze}
                onChange={(e) => updateConfig("forceReanalyze", e.target.checked)}
                className="w-4 h-4 rounded border-cyan-500/50 bg-slate-800/80 text-cyan-500 focus:ring-cyan-400/50"
              />
              <label htmlFor="forceReanalyze" className="text-sm text-slate-300 cursor-pointer">
                强制重新分析（勾选后将无视历史战报，重新消耗额度分析所有简历）
              </label>
            </div>
          </div>
        </motion.div>

        {/* 启动按钮 */}
        <div className="flex justify-center">
          <button
            onClick={handleStart}
            disabled={running}
            className="flex items-center gap-2 px-8 py-3 rounded-lg font-mono text-sm tracking-wider transition-all disabled:opacity-50 disabled:cursor-not-allowed"
            style={{
              background: "linear-gradient(135deg, rgba(0, 212, 255, 0.2) 0%, rgba(0, 255, 136, 0.15) 100%)",
              border: "1px solid rgba(0, 212, 255, 0.4)",
              boxShadow: "0 0 20px rgba(0, 212, 255, 0.2)",
              color: "#00d4ff",
            }}
          >
            {running ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                招聘引擎运行中...
              </>
            ) : (
              <>
                <Zap className="w-4 h-4" />
                启动招聘引擎
              </>
            )}
          </button>
        </div>

        {/* 赛博朋克终端 */}
        {logs.length > 0 && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="rounded-xl border border-cyan-500/40 bg-[#0a0e14] p-5 font-mono text-xs overflow-hidden"
            style={{
              boxShadow: "0 0 30px rgba(0, 212, 255, 0.12), inset 0 0 60px rgba(0, 212, 255, 0.03)",
            }}
          >
            <div className="text-cyan-400/80 mb-2 tracking-wider">[SYS.LOG] 流式输出</div>
            <div className="space-y-1 max-h-48 overflow-y-auto text-slate-300">
              {logs.map((line, i) => (
                <div key={i} className="text-cyan-300/90">
                  {line.startsWith("  └") ? (
                    <span className="text-emerald-400/90">{line}</span>
                  ) : (
                    line
                  )}
                </div>
              ))}
            </div>
            <div ref={logEndRef} />
          </motion.div>
        )}
      </div>
    </div>
  );
}
