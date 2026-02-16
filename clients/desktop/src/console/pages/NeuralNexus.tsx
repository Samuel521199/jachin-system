/**
 * Neural Nexus - 大脑扫描：记忆星云 3D + 当前模型与上下文
 * 使用 @react-three/fiber 实现 3D 记忆星云可视化
 */

import { useState, useEffect, useCallback } from "react";
import { motion } from "framer-motion";
import { Loader2 } from "lucide-react";
import { checkHealth, searchMemory, getConfig } from "../../lib/api";
import type { MemorySearchResult } from "../../lib/api";
import { ModelController } from "../components/ModelController";
import { MemoryNebula3D } from "../components/MemoryNebula3D";

export function NeuralNexus() {
  const [backendOk, setBackendOk] = useState<boolean | null>(null);
  const [loading, setLoading] = useState(true);
  const [searchMessage, setSearchMessage] = useState<string | null>(null);
  const [searchResults, setSearchResults] = useState<MemorySearchResult[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchKeyword, setSearchKeyword] = useState("");
  const [config, setConfig] = useState<{ model_name?: string } | null>(null);

  const handleMemorySearch = useCallback(async (keyword: string) => {
    setSearchLoading(true);
    setSearchMessage(null);
    setSearchResults([]);
    try {
      const res = await searchMemory(keyword);
      if (res?.results?.length) {
        setSearchResults(res.results);
        setSearchMessage(`${res.results.length} 条相关记忆`);
      } else {
        setSearchMessage(res?.message || "无相关记忆");
      }
    } catch (e) {
      setSearchMessage("搜索失败");
      setSearchResults([]);
    } finally {
      setSearchLoading(false);
    }
  }, []);

  const fetchConfig = useCallback(async () => {
    try {
      const c = await getConfig();
      setConfig({ model_name: c.model_name });
    } catch {
      setConfig(null);
    }
  }, []);

  useEffect(() => {
    fetchConfig();
    const t = setInterval(fetchConfig, 20000);
    return () => clearInterval(t);
  }, [fetchConfig]);

  useEffect(() => {
    const check = async () => {
      try {
        await checkHealth();
        setBackendOk(true);
      } catch {
        setBackendOk(false);
      } finally {
        setLoading(false);
      }
    };
    check();
    const t = setInterval(check, 10000);
    return () => clearInterval(t);
  }, []);

  return (
    <div className="h-full flex flex-col p-6 overflow-auto">
      <header className="flex-shrink-0 mb-6">
        <h1
          className="font-sci-fi text-2xl font-bold text-transparent bg-clip-text bg-gradient-to-r from-rose-400 to-rose-600"
          style={{ fontFamily: "Orbitron, sans-serif" }}
        >
          Neural Nexus
        </h1>
        <p className="text-slate-500 text-sm mt-0.5">模型与记忆 · 大脑扫描</p>
      </header>

      <motion.div
        className="flex-shrink-0 flex items-center gap-3 px-4 py-2 rounded-lg bg-white/5 border border-white/10 mb-6"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.2 }}
      >
        {loading ? (
          <Loader2 className="w-4 h-4 animate-spin text-slate-400" />
        ) : (
          <span className={backendOk ? "text-emerald-400" : "text-amber-400"}>●</span>
        )}
        <span className="text-slate-400 text-sm">jachin-brain</span>
        <span className="text-slate-600 text-sm">
          {loading ? "检测中…" : backendOk ? "在线" : "离线"}
        </span>
      </motion.div>

      <div className="flex-1 min-h-0 grid grid-cols-1 lg:grid-cols-2 gap-6">
        <motion.section
          className="glass-panel rounded-xl overflow-hidden flex flex-col min-h-[280px]"
          initial={{ opacity: 0, x: -12 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.3 }}
        >
          <div className="flex-shrink-0 p-4 pb-2 flex flex-col gap-3">
            <h2 className="font-mono text-xs uppercase tracking-wider text-slate-500">
              记忆星云
            </h2>
            <div className="flex gap-2">
              <input
                type="text"
                placeholder="搜索记忆…"
                value={searchKeyword}
                onChange={(e) => setSearchKeyword(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && searchKeyword.trim()) handleMemorySearch(searchKeyword.trim());
                }}
                className="flex-1 px-3 py-2 rounded-lg bg-white/5 border border-white/10 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-cyan-500/50 font-mono"
              />
              <button
                type="button"
                onClick={() => searchKeyword.trim() && handleMemorySearch(searchKeyword.trim())}
                className="px-3 py-2 rounded-lg bg-cyan-500/20 text-cyan-400 text-xs font-mono border border-cyan-500/30 hover:bg-cyan-500/30"
              >
                搜索
              </button>
            </div>
            {searchLoading && (
              <p className="text-xs font-mono text-cyan-400/80 flex items-center gap-2">
                <Loader2 className="w-3 h-3 animate-spin" /> 搜索中…
              </p>
            )}
            {searchMessage && !searchLoading && (
              <p className="text-xs font-mono text-cyan-400/80">{searchMessage}</p>
            )}
          </div>
          <div className="flex-1 min-h-[200px] relative">
            <MemoryNebula3D results={searchResults} className="absolute inset-0 rounded-b-xl" />
          </div>
          {searchResults.length > 0 && (
            <div className="flex-shrink-0 p-4 pt-2 space-y-2 max-h-32 overflow-y-auto custom-scrollbar">
              {searchResults.slice(0, 5).map((r) => (
                <div
                  key={r.id}
                  className="text-xs font-mono p-2 rounded bg-white/5 border border-white/10 text-slate-300 truncate"
                >
                  <span className="text-cyan-400/80">[{r.score.toFixed(2)}]</span> {r.text}
                </div>
              ))}
            </div>
          )}
        </motion.section>
        <motion.section
          className="flex flex-col min-h-0"
          initial={{ opacity: 0, x: 12 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.3, delay: 0.05 }}
        >
          <ModelController className="flex-1 min-h-0" modelName={config?.model_name} />
        </motion.section>
      </div>
    </div>
  );
}
