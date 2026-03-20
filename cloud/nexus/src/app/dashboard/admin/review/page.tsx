"use client";

import { useState, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import Navbar from "@/components/Navbar";
import { Scale, CheckCircle2, XCircle, FileJson, Package, Shield, Archive, RotateCcw } from "lucide-react";

type PendingPlugin = {
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
  manifest_json: Record<string, unknown> | null;
  status: string;
  reject_reason: string | null;
  created_at: string | null;
};

const ADMIN_TOKEN_KEY = "nexus_admin_token";

function getAdminHeaders(): HeadersInit {
  const token = typeof window !== "undefined" ? sessionStorage.getItem(ADMIN_TOKEN_KEY) : null;
  return token ? { "X-Admin-Token": token } : {};
}

function Toast({
  message,
  onDismiss,
  variant = "default",
}: {
  message: string;
  onDismiss: () => void;
  variant?: "default" | "success";
}) {
  useEffect(() => {
    const t = setTimeout(onDismiss, 4500);
    return () => clearTimeout(t);
  }, [onDismiss]);

  return (
    <motion.div
      initial={{ opacity: 0, y: -20, scale: 0.95 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: -10, scale: 0.98 }}
      transition={{ type: "spring", stiffness: 400, damping: 25 }}
      className={`fixed top-24 left-1/2 -translate-x-1/2 z-[100] px-6 py-4 rounded-xl backdrop-blur-xl ${
        variant === "success"
          ? "bg-gradient-to-r from-cyan-500/30 to-emerald-500/30 border border-cyan-400/50 shadow-[0_0_50px_rgba(34,211,238,0.4)] animate-pulse"
          : "bg-gradient-to-r from-cyan-500/20 to-emerald-500/20 border border-cyan-400/40 shadow-[0_0_40px_rgba(34,211,238,0.3)]"
      }`}
    >
      <p className="text-white font-medium flex items-center gap-2">
        {variant === "success" ? (
          <span className="text-2xl">✅</span>
        ) : (
          <span className="text-2xl">⚖️</span>
        )}
        {message}
      </p>
    </motion.div>
  );
}

type TabType = "pending" | "approved" | "archived";

export default function AdminReviewDashboardPage() {
  const [tab, setTab] = useState<TabType>("pending");
  const [plugins, setPlugins] = useState<PendingPlugin[]>([]);
  const [selected, setSelected] = useState<PendingPlugin | null>(null);
  const [loading, setLoading] = useState(true);
  const [toast, setToast] = useState<{ message: string; variant?: "default" | "success" } | null>(null);
  const [rejectModal, setRejectModal] = useState<{ plugin: PendingPlugin; reason: string } | null>(null);
  const [actioning, setActioning] = useState<string | null>(null);
  const [unlocked, setUnlocked] = useState(false);
  const [tokenInput, setTokenInput] = useState("");
  const [tokenError, setTokenError] = useState("");

  const fetchList = useCallback(async () => {
    setLoading(true);
    try {
      const url =
        tab === "pending"
          ? "/api/v1/admin/review"
          : `/api/v1/admin/plugins/list?status=${tab}`;
      const res = await fetch(url, {
        credentials: "include",
        headers: getAdminHeaders(),
      });
      if (res.status === 403) {
        setUnlocked(false);
        setPlugins([]);
        return;
      }
      const json = await res.json();
      if (json.success && Array.isArray(json.data)) {
        setPlugins(json.data);
        setUnlocked(true);
        if (selected && !json.data.find((p: PendingPlugin) => p.id === selected.id)) {
          setSelected(null);
        }
      } else {
        setPlugins([]);
      }
    } catch {
      setPlugins([]);
    } finally {
      setLoading(false);
    }
  }, [tab, selected]);

  const checkUnlock = useCallback(async () => {
    const res = await fetch("/api/v1/admin/check", {
      credentials: "include",
      headers: tokenInput ? { "X-Admin-Token": tokenInput } : {},
    });
    if (res.ok) {
      sessionStorage.setItem(ADMIN_TOKEN_KEY, tokenInput);
      setUnlocked(true);
      setTokenError("");
      fetchList();
    } else {
      setTokenError("Token 无效，请检查 NEXUS_ADMIN_SECRET 配置");
    }
  }, [tokenInput, fetchList]);

  useEffect(() => {
    const token = sessionStorage.getItem(ADMIN_TOKEN_KEY);
    if (token) {
      fetchList();
    } else {
      setLoading(false);
    }
  }, [fetchList]);

  const handleArchive = async (plugin: PendingPlugin) => {
    setActioning(plugin.id);
    try {
      const res = await fetch(`/api/v1/admin/plugins/${plugin.id}/archive`, {
        method: "POST",
        headers: getAdminHeaders(),
        credentials: "include",
      });
      const json = await res.json();
      if (json.success) {
        setToast({ message: "插件已下架归档，商城与 manifest 均不再展示", variant: "success" });
        await fetchList();
      } else {
        setToast({ message: `下架失败：${json.error ?? "未知错误"}` });
      }
    } catch (e) {
      setToast({ message: `下架失败：${e instanceof Error ? e.message : "网络错误"}` });
    } finally {
      setActioning(null);
    }
  };

  const handleRestore = async (plugin: PendingPlugin) => {
    setActioning(plugin.id);
    try {
      const res = await fetch(`/api/v1/admin/plugins/${plugin.id}/restore`, {
        method: "POST",
        headers: getAdminHeaders(),
        credentials: "include",
      });
      const json = await res.json();
      if (json.success) {
        setToast({ message: "插件已恢复上架", variant: "success" });
        await fetchList();
      } else {
        setToast({ message: `恢复失败：${json.error ?? "未知错误"}` });
      }
    } catch (e) {
      setToast({ message: `恢复失败：${e instanceof Error ? e.message : "网络错误"}` });
    } finally {
      setActioning(null);
    }
  };

  const handleApprove = async (plugin: PendingPlugin) => {
    setActioning(plugin.id);
    try {
      const res = await fetch("/api/v1/admin/review", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...getAdminHeaders() },
        credentials: "include",
        body: JSON.stringify({ plugin_id: plugin.id, action: "APPROVE" }),
      });
      const json = await res.json();
      if (json.success) {
        setToast({
          message: "✅ 该物资已面向全球 L2 开放同步",
          variant: "success",
        });
        await fetchList();
      } else {
        setToast({ message: `批准失败：${json.error ?? "未知错误"}` });
      }
    } catch (e) {
      setToast({ message: `批准失败：${e instanceof Error ? e.message : "网络错误"}` });
    } finally {
      setActioning(null);
    }
  };

  const handleRejectSubmit = async () => {
    if (!rejectModal) return;
    setActioning(rejectModal.plugin.id);
    try {
      const res = await fetch("/api/v1/admin/review", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...getAdminHeaders() },
        credentials: "include",
        body: JSON.stringify({
          plugin_id: rejectModal.plugin.id,
          action: "REJECT",
          reason: rejectModal.reason,
        }),
      });
      const json = await res.json();
      if (json.success) {
        setToast({ message: "已驳回该插件请求。" });
        setRejectModal(null);
        await fetchList();
      } else {
        setToast({ message: `驳回失败：${json.error ?? "未知错误"}` });
      }
    } catch (e) {
      setToast({ message: `驳回失败：${e instanceof Error ? e.message : "网络错误"}` });
    } finally {
      setActioning(null);
    }
  };

  if (!unlocked) {
    return (
      <div className="min-h-screen relative overflow-hidden">
        <div
          className="fixed inset-0 -z-10"
          style={{
            background: `linear-gradient(rgba(255,255,255,0.02) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.02) 1px, transparent 1px)`,
            backgroundSize: "48px 48px",
            backgroundColor: "#030712",
          }}
        />
        <Navbar />
        <main className="pt-32 pb-16 px-6 max-w-md mx-auto">
          <div className="rounded-2xl border border-amber-500/30 bg-amber-500/5 p-8">
            <h2 className="text-lg font-bold text-amber-400 flex items-center gap-2 mb-2">
              <Shield className="h-5 w-5" />
              管理员验证
            </h2>
            <p className="text-white/60 text-sm mb-4">
              此页面仅限 isRoot 用户访问。请输入管理员 Token 解锁。
            </p>
            <input
              type="password"
              value={tokenInput}
              onChange={(e) => setTokenInput(e.target.value)}
              placeholder="NEXUS_ADMIN_SECRET"
              className="w-full px-4 py-3 rounded-lg bg-black/40 border border-white/10 text-white placeholder-white/30 focus:outline-none focus:border-amber-400/50"
            />
            {tokenError && <p className="text-red-400 text-sm mt-2">{tokenError}</p>}
            <button
              onClick={checkUnlock}
              className="mt-4 w-full px-4 py-3 rounded-lg bg-amber-500/20 text-amber-300 border border-amber-400/40 hover:bg-amber-500/30 transition-colors font-medium"
            >
              解锁
            </button>
          </div>
        </main>
      </div>
    );
  }

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
            radial-gradient(ellipse 80% 60% at 100% 80%, rgba(139, 92, 246, 0.08) 0%, transparent 50%),
            radial-gradient(ellipse 60% 50% at 0% 100%, rgba(220, 38, 38, 0.04) 0%, transparent 50%)
          `,
        }}
      />

      <Navbar />

      <main className="pt-24 pb-16 px-6 max-w-7xl mx-auto">
        <header className="mb-10">
          <h1 className="text-2xl font-bold text-white tracking-tight mb-1 flex items-center gap-3">
            <Scale className="h-8 w-8 text-amber-400" />
            法律审核中心
          </h1>
          <p className="text-white/50 text-sm font-mono mb-4">
            待审插件 · 已上架管理 · 已归档恢复 · 仅 isRoot
          </p>
          <div className="flex gap-2">
            {(["pending", "approved", "archived"] as const).map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                  tab === t
                    ? "bg-cyan-500/20 text-cyan-300 border border-cyan-400/50"
                    : "bg-white/5 text-white/60 border border-white/10 hover:bg-white/10 hover:text-white/80"
                }`}
              >
                {t === "pending" ? "待审" : t === "approved" ? "已上架" : "已归档"}
              </button>
            ))}
          </div>
        </header>

        <AnimatePresence>
          {toast && (
            <motion.div key="toast" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
              <Toast
                message={toast.message}
                variant={toast.variant}
                onDismiss={() => setToast(null)}
              />
            </motion.div>
          )}
        </AnimatePresence>

        {loading ? (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            <div className="lg:col-span-1 space-y-4">
              {[1, 2, 3].map((i) => (
                <div key={i} className="h-24 rounded-xl border border-white/10 bg-white/[0.02] animate-pulse" />
              ))}
            </div>
            <div className="lg:col-span-2 h-96 rounded-xl border border-white/10 bg-white/[0.02] animate-pulse" />
          </div>
        ) : plugins.length === 0 ? (
          <div className="rounded-xl border border-white/10 bg-white/[0.02] p-16 text-center">
            <Scale className="h-16 w-16 text-white/20 mx-auto mb-4" />
            <p className="text-white/50 text-lg">
              {tab === "pending"
                ? "暂无待审插件"
                : tab === "approved"
                  ? "暂无已上架插件"
                  : "暂无已归档插件"}
            </p>
            <p className="text-white/30 text-sm mt-2">
              {tab === "pending"
                ? "开发者发布 PUBLIC 插件后将在此排队"
                : tab === "approved"
                  ? "批准后的插件将在此展示，可下架归档"
                  : "下架后的插件将在此展示，可恢复上架"}
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            {/* 左侧：待审清单 */}
            <div className="lg:col-span-1 space-y-3 max-h-[calc(100vh-12rem)] overflow-y-auto">
              {plugins.map((plugin) => (
                <motion.button
                  key={plugin.id}
                  onClick={() => setSelected(plugin)}
                  className={`w-full text-left p-4 rounded-xl border transition-all ${
                    selected?.id === plugin.id
                      ? "border-cyan-400/50 bg-cyan-500/10"
                      : "border-white/10 bg-white/[0.02] hover:bg-white/[0.04]"
                  }`}
                >
                  <div className="flex items-start gap-3">
                    <div className="w-10 h-10 rounded-lg bg-white/10 flex items-center justify-center shrink-0">
                      <Package className="h-5 w-5 text-cyan-400" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="font-medium text-white truncate">{plugin.name}</p>
                      <p className="text-white/50 text-xs font-mono truncate">
                        {plugin.developer_id ?? "—"}
                      </p>
                      <p className="text-white/40 text-xs mt-1">
                        {plugin.created_at ? new Date(plugin.created_at).toLocaleString("zh-CN") : "—"}
                      </p>
                    </div>
                  </div>
                </motion.button>
              ))}
            </div>

            {/* 右侧：详情预览 */}
            <div className="lg:col-span-2">
              {selected ? (
                <motion.div
                  key={selected.id}
                  initial={{ opacity: 0, x: 20 }}
                  animate={{ opacity: 1, x: 0 }}
                  className="rounded-xl border border-white/10 bg-white/[0.02] overflow-hidden backdrop-blur-sm"
                >
                  <div className="p-6 border-b border-white/10">
                    <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-4">
                      <div>
                        <h2 className="text-xl font-bold text-white">{selected.name}</h2>
                        <p className="text-white/50 text-sm font-mono mt-1">
                          {selected.plugin_id} · v{selected.version}
                        </p>
                        <p className="text-white/40 text-sm mt-2">
                          开发者 ID: {selected.developer_id ?? "—"}
                        </p>
                        <div className="flex flex-wrap gap-2 mt-3">
                          <span className="px-2 py-0.5 rounded text-xs bg-cyan-500/20 text-cyan-300">
                            {selected.item_type}
                          </span>
                          <span className="px-2 py-0.5 rounded text-xs bg-violet-500/20 text-violet-300">
                            {selected.runtime_tier}
                          </span>
                          <span className="px-2 py-0.5 rounded text-xs bg-white/10 text-white/70">
                            ¥{selected.price_monthly}/月
                          </span>
                        </div>
                      </div>
                      <div className="flex gap-3 shrink-0">
                        {tab === "pending" ? (
                          <>
                            <button
                              onClick={() => handleApprove(selected)}
                              disabled={!!actioning}
                              className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl bg-cyan-500/20 text-cyan-300 border border-cyan-400/40 hover:bg-cyan-500/30 disabled:opacity-50 transition-colors font-medium"
                            >
                              <CheckCircle2 className="h-4 w-4" />
                              批准入驻
                            </button>
                            <button
                              onClick={() => setRejectModal({ plugin: selected, reason: "" })}
                              disabled={!!actioning}
                              className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl bg-red-500/20 text-red-300 border border-red-400/40 hover:bg-red-500/30 disabled:opacity-50 transition-colors font-medium"
                            >
                              <XCircle className="h-4 w-4" />
                              驳回申请
                            </button>
                          </>
                        ) : tab === "approved" ? (
                          <button
                            onClick={() => handleArchive(selected)}
                            disabled={!!actioning}
                            className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl bg-amber-500/20 text-amber-300 border border-amber-400/40 hover:bg-amber-500/30 disabled:opacity-50 transition-colors font-medium"
                          >
                            <Archive className="h-4 w-4" />
                            下架归档
                          </button>
                        ) : (
                          <button
                            onClick={() => handleRestore(selected)}
                            disabled={!!actioning}
                            className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl bg-emerald-500/20 text-emerald-300 border border-emerald-400/40 hover:bg-emerald-500/30 disabled:opacity-50 transition-colors font-medium"
                          >
                            <RotateCcw className="h-4 w-4" />
                            恢复上架
                          </button>
                        )}
                      </div>
                    </div>
                  </div>

                  {selected.description && (
                    <div className="px-6 py-3 border-b border-white/5">
                      <p className="text-white/60 text-sm">{selected.description}</p>
                    </div>
                  )}

                  <div className="p-4 border-b border-white/5">
                    <div className="flex items-center gap-2 mb-2">
                      <Shield className="h-4 w-4 text-amber-400" />
                      <span className="text-sm font-medium text-white/80">权限声明</span>
                    </div>
                    <p className="text-white/50 text-xs">
                      {(selected.manifest_json as Record<string, unknown>)?.permissions
                        ? JSON.stringify((selected.manifest_json as Record<string, unknown>).permissions)
                        : "无特殊权限"}
                    </p>
                  </div>

                  <div className="p-4 bg-black/20">
                    <div className="flex items-center gap-2 mb-2">
                      <FileJson className="h-4 w-4 text-amber-400" />
                      <span className="text-sm font-medium text-white/80">plugin.json 完整内容</span>
                    </div>
                    <pre className="text-xs text-white/70 overflow-x-auto p-4 rounded-lg bg-black/40 border border-white/5 font-mono max-h-64 overflow-y-auto">
                      {JSON.stringify(selected.manifest_json ?? {}, null, 2)}
                    </pre>
                  </div>
                </motion.div>
              ) : (
                <div className="rounded-xl border border-white/10 bg-white/[0.02] p-16 text-center">
                  <Package className="h-12 w-12 text-white/20 mx-auto mb-3" />
                  <p className="text-white/50">选择左侧插件查看详情</p>
                </div>
              )}
            </div>
          </div>
        )}
      </main>

      <AnimatePresence>
        {rejectModal && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
            onClick={() => !actioning && setRejectModal(null)}
          >
            <motion.div
              initial={{ scale: 0.95, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.95, opacity: 0 }}
              onClick={(e) => e.stopPropagation()}
              className="rounded-2xl border border-white/10 bg-slate-900/95 backdrop-blur-xl p-6 max-w-md w-full shadow-2xl"
            >
              <h3 className="text-lg font-bold text-white mb-2">驳回申请</h3>
              <p className="text-white/60 text-sm mb-4">
                插件：{rejectModal.plugin.name} ({rejectModal.plugin.plugin_id})
              </p>
              <textarea
                value={rejectModal.reason}
                onChange={(e) =>
                  setRejectModal((prev) => (prev ? { ...prev, reason: e.target.value } : null))
                }
                placeholder="驳回理由（可选，将通知开发者）"
                className="w-full h-24 px-4 py-3 rounded-lg bg-black/40 border border-white/10 text-white/90 text-sm placeholder-white/30 resize-none focus:outline-none focus:border-red-400/40"
              />
              <div className="flex gap-3 mt-4">
                <button
                  onClick={() => setRejectModal(null)}
                  disabled={!!actioning}
                  className="flex-1 px-4 py-2.5 rounded-xl bg-white/10 text-white/80 hover:bg-white/15 disabled:opacity-50 transition-colors"
                >
                  取消
                </button>
                <button
                  onClick={handleRejectSubmit}
                  disabled={!!actioning}
                  className="flex-1 px-4 py-2.5 rounded-xl bg-red-500/20 text-red-300 border border-red-400/40 hover:bg-red-500/30 disabled:opacity-50 transition-colors font-medium"
                >
                  {actioning ? "处理中..." : "确认驳回"}
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
