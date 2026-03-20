"use client";

import { useState, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import Navbar from "@/components/Navbar";
import { Package, Archive } from "lucide-react";

type PluginItem = {
  id: string;
  plugin_id: string;
  version: string;
  item_type: string;
  name: string;
  description: string | null;
  developer_id: string | null;
  visibility: string;
  price_monthly: number;
  runtime_tier: string;
  package_url: string | null;
  status: string;
  created_at: string | null;
};

const DEVELOPER_ID_KEY = "nexus_developer_id";

function getDeveloperId(): string {
  if (typeof document === "undefined") return "";
  const match = document.cookie.match(/nexus_developer_id=([^;]+)/);
  const fromCookie = match?.[1] ? decodeURIComponent(match[1]) : "";
  return fromCookie || (typeof sessionStorage !== "undefined" ? sessionStorage.getItem(DEVELOPER_ID_KEY) ?? "" : "");
}

function persistDeveloperId(id: string) {
  if (typeof document !== "undefined") {
    document.cookie = `nexus_developer_id=${encodeURIComponent(id)}; path=/; max-age=31536000`;
  }
  if (typeof sessionStorage !== "undefined") {
    sessionStorage.setItem(DEVELOPER_ID_KEY, id);
  }
}

export default function DeveloperPluginsPage() {
  const [developerId, setDeveloperIdState] = useState("");
  const [developerIdInput, setDeveloperIdInput] = useState("");
  const [plugins, setPlugins] = useState<PluginItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [toast, setToast] = useState<{ message: string; type: "success" | "error" } | null>(null);
  const [actioning, setActioning] = useState<string | null>(null);

  useEffect(() => {
    setDeveloperIdState(getDeveloperId());
    setDeveloperIdInput(getDeveloperId());
  }, []);

  const fetchPlugins = useCallback(async () => {
    const devId = developerId || developerIdInput;
    if (!devId.trim()) {
      setPlugins([]);
      return;
    }
    setLoading(true);
    try {
      const res = await fetch(`/api/v1/developer/plugins?developer_id=${encodeURIComponent(devId)}`, {
        credentials: "include",
      });
      const json = await res.json();
      if (json.success && Array.isArray(json.data)) {
        setPlugins(json.data);
        if (!developerId) setDeveloperIdState(devId);
      } else {
        setPlugins([]);
        if (res.status === 401) {
          setToast({ message: "请先输入开发者 ID", type: "error" });
        }
      }
    } catch {
      setPlugins([]);
      setToast({ message: "加载失败", type: "error" });
    } finally {
      setLoading(false);
    }
  }, [developerId, developerIdInput]);

  useEffect(() => {
    if (developerId) fetchPlugins();
  }, [developerId, fetchPlugins]);

  const handleUnlock = async () => {
    const id = developerIdInput.trim();
    if (!id) {
      setToast({ message: "请输入开发者 ID", type: "error" });
      return;
    }
    persistDeveloperId(id);
    setDeveloperIdState(id);
    setLoading(true);
    try {
      const res = await fetch(`/api/v1/developer/plugins?developer_id=${encodeURIComponent(id)}`, {
        credentials: "include",
      });
      const json = await res.json();
      if (json.success && Array.isArray(json.data)) {
        setPlugins(json.data);
      } else {
        setPlugins([]);
      }
    } catch {
      setPlugins([]);
    } finally {
      setLoading(false);
    }
  };

  const handleUnpublish = async (plugin: PluginItem) => {
    const devId = developerId || getDeveloperId();
    if (!devId) {
      setToast({ message: "请先输入开发者 ID 解锁", type: "error" });
      return;
    }
    setActioning(plugin.id);
    try {
      const res = await fetch("/api/v1/store/unpublish", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Developer-Id": devId,
        },
        credentials: "include",
        body: JSON.stringify({ plugin_id: plugin.id }),
      });
      const json = await res.json();
      if (json.success) {
        setToast({ message: "插件已下架归档", type: "success" });
        await fetchPlugins();
      } else {
        setToast({ message: json.message || json.error || "下架失败", type: "error" });
      }
    } catch (e) {
      setToast({ message: e instanceof Error ? e.message : "网络错误", type: "error" });
    } finally {
      setActioning(null);
    }
  };

  const hasDevId = !!developerId || !!developerIdInput;

  return (
    <div className="min-h-screen relative overflow-hidden">
      <div
        className="fixed inset-0 -z-10"
        style={{
          background: `
            linear-gradient(rgba(255,255,255,0.02) 1px, transparent 1px),
            linear-gradient(90deg, rgba(255,255,255,0.02) 1px, transparent 1px)
          `,
          backgroundSize: "48px 48px",
          backgroundColor: "#030712",
        }}
      />
      <div
        className="fixed inset-0 -z-10"
        style={{
          background: `
            radial-gradient(ellipse 100% 80% at 50% 0%, rgba(34, 211, 238, 0.06) 0%, transparent 50%),
            radial-gradient(ellipse 80% 60% at 100% 80%, rgba(139, 92, 246, 0.08) 0%, transparent 50%)
          `,
        }}
      />

      <Navbar />

      <main className="pt-24 pb-16 px-6 max-w-4xl mx-auto">
        <header className="mb-10">
          <h1 className="text-2xl font-bold text-white tracking-tight mb-1 flex items-center gap-3">
            <Package className="h-8 w-8 text-cyan-400" />
            我的作品
          </h1>
          <p className="text-white/50 text-sm font-mono mb-4">
            管理已发布的 Skill / MCP，可自助下架
          </p>

          {!hasDevId ? (
            <div className="flex gap-3 items-center">
              <input
                type="text"
                value={developerIdInput}
                onChange={(e) => setDeveloperIdInput(e.target.value)}
                placeholder="开发者 ID（如 dev-demo-001 或 publish 时填写的 developer_id）"
                className="flex-1 px-4 py-3 rounded-lg bg-black/40 border border-white/10 text-white placeholder-white/30 focus:outline-none focus:border-cyan-400/50"
              />
              <button
                onClick={handleUnlock}
                className="px-5 py-3 rounded-lg bg-cyan-500/20 text-cyan-300 border border-cyan-400/40 hover:bg-cyan-500/30 transition-colors font-medium"
              >
                查看
              </button>
            </div>
          ) : (
            <div className="flex items-center gap-3">
              <span className="text-white/60 text-sm font-mono">
                开发者 ID: {developerId || developerIdInput}
              </span>
              <button
                onClick={() => {
                  setDeveloperIdState("");
                  setDeveloperIdInput("");
                  setPlugins([]);
                }}
                className="text-xs text-white/40 hover:text-white/70"
              >
                切换
              </button>
            </div>
          )}
        </header>

        <AnimatePresence>
          {toast && (
            <motion.div
              initial={{ opacity: 0, y: -10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              className={`fixed top-24 left-1/2 -translate-x-1/2 z-[100] px-6 py-4 rounded-xl ${
                toast.type === "success"
                  ? "bg-emerald-500/20 border border-emerald-400/50"
                  : "bg-red-500/20 border border-red-400/50"
              }`}
            >
              <p className="text-white font-medium">{toast.message}</p>
            </motion.div>
          )}
        </AnimatePresence>

        {loading ? (
          <div className="space-y-4">
            {[1, 2, 3].map((i) => (
              <div key={i} className="h-24 rounded-xl border border-white/10 bg-white/[0.02] animate-pulse" />
            ))}
          </div>
        ) : !hasDevId ? (
          <div className="rounded-xl border border-white/10 bg-white/[0.02] p-16 text-center">
            <Package className="h-16 w-16 text-white/20 mx-auto mb-4" />
            <p className="text-white/50 text-lg">请输入开发者 ID 查看作品</p>
            <p className="text-white/30 text-sm mt-2">
              publish 时填写的 developer_id，或与收益中心一致的 ID
            </p>
          </div>
        ) : plugins.length === 0 ? (
          <div className="rounded-xl border border-white/10 bg-white/[0.02] p-16 text-center">
            <Package className="h-16 w-16 text-white/20 mx-auto mb-4" />
            <p className="text-white/50 text-lg">暂无作品</p>
            <p className="text-white/30 text-sm mt-2">
              使用 jachin publish 发布后，将在此展示
            </p>
          </div>
        ) : (
          <div className="space-y-4">
            {plugins.map((plugin) => (
              <motion.div
                key={plugin.id}
                layout
                initial={{ opacity: 0, y: 12 }}
                animate={{ opacity: 1, y: 0 }}
                className="rounded-xl border border-white/10 bg-white/[0.02] p-5 hover:bg-white/[0.04] transition-colors"
              >
                <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 mb-2">
                      <span
                        className={`inline-flex px-2.5 py-0.5 rounded-full text-xs font-mono ${
                          plugin.item_type === "SKILL"
                            ? "bg-cyan-500/20 text-cyan-400"
                            : "bg-violet-500/20 text-violet-400"
                        }`}
                      >
                        {plugin.item_type}
                      </span>
                      <span
                        className={`text-xs px-2 py-0.5 rounded ${
                          plugin.status === "approved"
                            ? "bg-emerald-500/20 text-emerald-400"
                            : plugin.status === "archived"
                              ? "bg-amber-500/20 text-amber-400"
                              : "bg-white/10 text-white/60"
                        }`}
                      >
                        {plugin.status === "approved" ? "已上架" : plugin.status === "archived" ? "已归档" : plugin.status}
                      </span>
                    </div>
                    <h3 className="text-lg font-semibold text-white truncate">{plugin.name}</h3>
                    <p className="text-white/50 text-sm font-mono truncate">
                      {plugin.plugin_id} · v{plugin.version}
                    </p>
                  </div>
                  <div className="shrink-0">
                    {plugin.status === "approved" ? (
                      <button
                        onClick={() => handleUnpublish(plugin)}
                        disabled={!!actioning}
                        className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl bg-amber-500/20 text-amber-300 border border-amber-400/40 hover:bg-amber-500/30 disabled:opacity-50 transition-colors font-medium"
                      >
                        <Archive className="h-4 w-4" />
                        {actioning === plugin.id ? "处理中..." : "下架"}
                      </button>
                    ) : (
                      <span className="text-white/40 text-sm">下架后需联系管理员恢复</span>
                    )}
                  </div>
                </div>
              </motion.div>
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
