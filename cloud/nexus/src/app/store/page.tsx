"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { motion, AnimatePresence } from "framer-motion";
import Navbar from "@/components/Navbar";
import { useNexusUiLang } from "@/components/NexusUiLangProvider";
import { nexusStore } from "@/lib/nexus-ui-i18n";
import { getMcpStoreDisplay } from "@/lib/store-mcp-catalog-i18n";

type TabType = "SKILL" | "MCP" | "TOOL" | "MODEL";

type CatalogRequiredModel = {
  model_id: string;
  plugin_id: string | null;
  name: string;
  package_url: string | null;
  version: string | null;
};

type CatalogItem = {
  id: string;
  /** 与 plugin.json id 一致，用于 MCP 卡片 i18n 映射 */
  plugin_id?: string | null;
  item_type: string;
  name: string;
  description: string | null;
  developer_id: string | null;
  price_monthly: number;
  runtime_tier: string;
  required_mcps: string[];
  required_models?: CatalogRequiredModel[];
  package_url: string | null;
  created_at: string | null;
  /** L3 运行时内置 core:/util:/sys:，非独立下载包 */
  runtime_builtin?: boolean;
  tool_id?: string | null;
};

// 与 L1 默认用户 ID 一致，确保 L2 配对后 manifest 能拉取到订阅（l1_user_id = 00000000-0000-0000-0000-000000000001）
const DEMO_TENANT_ID = "00000000-0000-0000-0000-000000000001";

function SkeletonCard() {
  return (
    <div className="rounded-xl border border-white/10 bg-white/5 p-5 animate-pulse">
      <div className="flex items-start justify-between gap-3 mb-3">
        <div className="h-5 w-32 rounded bg-white/10" />
        <div className="h-5 w-16 rounded-full bg-white/10" />
      </div>
      <div className="h-4 w-full rounded bg-white/10 mb-2" />
      <div className="h-4 w-3/4 rounded bg-white/10 mb-4" />
      <div className="h-3 w-24 rounded bg-white/10 mb-4" />
      <div className="flex justify-end">
        <div className="h-9 w-24 rounded-lg bg-white/10" />
      </div>
    </div>
  );
}

function ProductCard({
  item,
  isOwned,
  isLoading,
  installingModelKey,
  onSubscribe,
  onInstallModel,
  onGoModelAssetsTab,
}: {
  item: CatalogItem;
  isOwned: boolean;
  isLoading: boolean;
  installingModelKey: string | null;
  onSubscribe: (item: CatalogItem) => void;
  onInstallModel: (model: CatalogRequiredModel) => void;
  onGoModelAssetsTab: () => void;
}) {
  const { lang } = useNexusUiLang();
  const t = nexusStore[lang];
  const itemKind: "skill" | "mcp" | "tool" | "model" =
    item.item_type === "SKILL"
      ? "skill"
      : item.item_type === "MCP"
        ? "mcp"
        : item.item_type === "MODEL"
          ? "model"
          : "tool";
  const isRuntimeBuiltin = Boolean(item.runtime_builtin);
  const isFree = item.price_monthly === 0;
  const canInstallLocally = itemKind === "skill" || itemKind === "mcp" || itemKind === "model";
  const disabled = isRuntimeBuiltin || isLoading || (isOwned && !canInstallLocally);
  const requiredModels = item.item_type === "MCP" ? item.required_models ?? [] : [];
  const hasRequiredModels = requiredModels.length > 0;

  const mcpDisplay =
    itemKind === "mcp"
      ? getMcpStoreDisplay(item.plugin_id, item.name, item.description, lang)
      : null;

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.25 }}
      className="group rounded-xl border border-white/10 bg-white/[0.02] hover:bg-white/[0.06] hover:border-cyan-500/30 transition-all duration-300 p-5 backdrop-blur-sm"
    >
      <div className="flex items-start justify-between gap-3 mb-3">
        <span
          className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-mono uppercase tracking-wider ${
            itemKind === "skill"
              ? "bg-cyan-500/20 text-cyan-400 border border-cyan-400/30"
              : itemKind === "mcp"
                ? "bg-violet-500/20 text-violet-400 border border-violet-400/30"
                : itemKind === "model"
                  ? "bg-emerald-500/20 text-emerald-400 border border-emerald-400/30"
                  : "bg-amber-500/20 text-amber-400 border border-amber-400/30"
          }`}
        >
          {itemKind === "skill" ? "⚡ SKILL" : itemKind === "mcp" ? "🔌 MCP" : itemKind === "model" ? "🧠 MODEL" : "🔧 TOOL"}
          {isRuntimeBuiltin && itemKind === "tool" ? (
            <span className="ml-1 normal-case text-[10px] text-emerald-400/90 border border-emerald-500/30 rounded px-1.5 py-0">
              {t.toolBuiltinBadge}
            </span>
          ) : null}
        </span>
        <span className="text-xs text-white/40 font-mono">
          {item.runtime_tier.replace("_", " ")}
        </span>
      </div>
      {itemKind === "mcp" && hasRequiredModels ? (
        <div className="mb-3 inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-mono border border-amber-400/30 bg-amber-500/10 text-amber-200/90">
          🧠 {t.mcpNeedsLocalModels}
        </div>
      ) : null}

      <h3 className="text-lg font-semibold text-white mb-2 tracking-tight group-hover:text-cyan-100 transition-colors">
        {mcpDisplay ? mcpDisplay.title : item.name}
      </h3>

      <div className="mb-4 min-h-[2.5rem] space-y-1.5">
        <p className="text-sm text-white/60 leading-relaxed line-clamp-3">
          {mcpDisplay
            ? mcpDisplay.tagline || t.noDesc
            : item.description || t.noDesc}
        </p>
        {mcpDisplay?.technicalNote ? (
          <p className="text-xs text-white/35 leading-snug line-clamp-2 font-mono">
            {mcpDisplay.technicalNote}
          </p>
        ) : null}
      </div>
      {isRuntimeBuiltin ? (
        <p className="text-xs text-emerald-500/70 font-mono mb-3">{t.toolBuiltinHint}</p>
      ) : null}
      {itemKind === "mcp" && hasRequiredModels ? (
        <div className="mb-4 rounded-lg border border-violet-400/25 bg-violet-500/10 p-3 space-y-2">
          <p className="text-xs text-violet-100/95 leading-relaxed">{t.mcpModelRecommendTitle}</p>
          <div className="space-y-2">
            {requiredModels.map((m) => {
              const installKey = m.plugin_id ?? m.model_id;
              const isInstallingThisModel = installingModelKey === installKey;
              return (
                <div
                  key={`${item.id}-${m.model_id}`}
                  className="flex items-center justify-between gap-2 rounded-md border border-white/10 bg-black/20 px-2.5 py-2"
                >
                  <div className="min-w-0">
                    <p className="text-xs font-medium text-white truncate">{m.name}</p>
                    <p className="text-[11px] text-white/45 font-mono truncate">
                      {m.package_url ? `id: ${m.model_id}` : t.modelMissingPkg}
                    </p>
                  </div>
                  {m.package_url && m.plugin_id ? (
                    <button
                      onClick={() => onInstallModel(m)}
                      disabled={Boolean(installingModelKey)}
                      className={`shrink-0 px-2.5 py-1.5 rounded-md text-xs font-medium transition-colors ${
                        installingModelKey
                          ? "bg-white/10 text-white/45 border border-white/10 cursor-not-allowed"
                          : "bg-emerald-500/20 text-emerald-300 border border-emerald-400/30 hover:bg-emerald-500/30"
                      }`}
                    >
                      {isInstallingThisModel ? t.modelInstalling : t.modelInstallNow}
                    </button>
                  ) : (
                    <button
                      onClick={onGoModelAssetsTab}
                      className="shrink-0 px-2.5 py-1.5 rounded-md text-xs font-medium bg-cyan-500/20 text-cyan-300 border border-cyan-400/30 hover:bg-cyan-500/30 transition-colors"
                    >
                      {t.modelGoAssetTab}
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      ) : null}

      <div className="flex items-center gap-2 text-xs text-white/40 mb-4">
        <span className="font-mono">
          {item.developer_id || "Jachin"}
        </span>
      </div>

      <div className="flex items-center justify-between gap-2">
        <span className="text-sm font-mono">
          {isFree ? (
            <span className="text-emerald-400">{t.free}</span>
          ) : (
            <span className="text-white/80">
              ¥{item.price_monthly} <span className="text-white/50">{t.perMonth}</span>
            </span>
          )}
        </span>
        <button
          onClick={() => !disabled && onSubscribe(item)}
          disabled={disabled}
          className={`px-4 py-2 rounded-lg text-sm font-medium transition-all duration-200 ${
            isRuntimeBuiltin
              ? "bg-emerald-500/15 text-emerald-400/90 border border-emerald-500/35 cursor-default"
              : disabled
                ? "bg-white/10 text-white/40 border border-white/10 cursor-not-allowed"
                : itemKind === "skill"
                  ? "bg-cyan-500/20 text-cyan-400 border border-cyan-400/40 hover:bg-cyan-500/30 hover:shadow-[0_0_20px_rgba(34,211,238,0.2)]"
                : itemKind === "mcp"
                  ? "bg-violet-500/20 text-violet-400 border border-violet-400/40 hover:bg-violet-500/30 hover:shadow-[0_0_20px_rgba(139,92,246,0.2)]"
                  : itemKind === "model"
                    ? "bg-emerald-500/20 text-emerald-400 border border-emerald-400/40 hover:bg-emerald-500/30 hover:shadow-[0_0_20px_rgba(16,185,129,0.2)]"
                    : "bg-amber-500/20 text-amber-400 border border-amber-400/40 hover:bg-amber-500/30 hover:shadow-[0_0_20px_rgba(245,158,11,0.2)]"
          }`}
        >
          {isLoading ? (
            <span className="inline-flex items-center gap-1.5">
              <motion.span
                animate={{ rotate: 360 }}
                transition={{ duration: 0.8, repeat: Infinity, ease: "linear" }}
                className="inline-block w-3.5 h-3.5 border-2 border-current border-t-transparent rounded-full"
              />
              {t.subscribing}
            </span>
          ) : isRuntimeBuiltin ? (
            t.toolPreinstalled
          ) : isOwned && canInstallLocally ? (
            t.syncLocal
          ) : isOwned ? (
            t.owned
          ) : (
            t.getSubscribe
          )}
        </button>
      </div>
    </motion.div>
  );
}

function Toast({
  message,
  type,
  onDismiss,
}: {
  message: string;
  type: "success" | "info" | "error";
  onDismiss: () => void;
}) {
  useEffect(() => {
    const t = setTimeout(onDismiss, type === "success" ? 4000 : 3500);
    return () => clearTimeout(t);
  }, [onDismiss, type]);

  const styles =
    type === "success"
      ? "from-cyan-500/20 to-emerald-500/20 border-cyan-400/40 shadow-[0_0_40px_rgba(34,211,238,0.3)]"
      : type === "error"
        ? "from-amber-500/20 to-red-500/20 border-amber-400/40 shadow-[0_0_40px_rgba(251,191,36,0.2)]"
        : "from-white/10 to-white/5 border-white/20 shadow-[0_0_30px_rgba(255,255,255,0.1)]";

  const icon = type === "success" ? "✅" : type === "error" ? "⚠️" : "ℹ️";

  return (
    <motion.div
      initial={{ opacity: 0, y: -20, scale: 0.95 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: -10, scale: 0.98 }}
      transition={{ type: "spring", stiffness: 400, damping: 25 }}
      className={`fixed top-24 left-1/2 -translate-x-1/2 z-[100] px-6 py-4 rounded-xl bg-gradient-to-r border backdrop-blur-xl ${styles}`}
    >
      <p className="text-white font-medium flex items-center gap-2">
        <span className="text-2xl">{icon}</span>
        {message}
      </p>
    </motion.div>
  );
}

function EmptyState({ tab }: { tab: TabType }) {
  const { lang } = useNexusUiLang();
  const t = nexusStore[lang];
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      className="flex flex-col items-center justify-center py-24 text-center"
    >
      <div className="w-20 h-20 rounded-2xl border border-dashed border-white/15 flex items-center justify-center mb-6 bg-white/[0.02]">
        <span className="text-4xl">
          {tab === "SKILL" ? "⚡" : tab === "MCP" ? "🔌" : tab === "MODEL" ? "🧠" : "🔧"}
        </span>
      </div>
      <p className="text-white/50 text-lg mb-2">
        {tab === "SKILL" ? t.emptySkill : tab === "MCP" ? t.emptyMcp : tab === "MODEL" ? "暂无模型资产" : t.emptyTool}
      </p>
      <p className="text-white/30 text-sm max-w-sm">
        {t.emptyHint}
      </p>
    </motion.div>
  );
}

export default function StorePage() {
  const { lang } = useNexusUiLang();
  const t = nexusStore[lang];
  const router = useRouter();
  const [tab, setTab] = useState<TabType>("SKILL");
  const [items, setItems] = useState<CatalogItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [total, setTotal] = useState(0);
  const [ownedIds, setOwnedIds] = useState<Set<string>>(new Set());
  const [subscribingId, setSubscribingId] = useState<string | null>(null);
  const [installingModelKey, setInstallingModelKey] = useState<string | null>(null);
  const [toast, setToast] = useState<{ message: string; type: "success" | "info" | "error" } | null>(null);
  const [catalogNote, setCatalogNote] = useState<string | null>(null);

  // 演示模式：确保有 tenant cookie，否则订阅会 401
  useEffect(() => {
    if (typeof document === "undefined") return;
    const hasTenant = document.cookie.includes("nexus_tenant_id=");
    if (!hasTenant) {
      document.cookie = `nexus_tenant_id=${encodeURIComponent(DEMO_TENANT_ID)}; path=/; max-age=31536000`;
    }
  }, []);

  const fetchCatalog = async (itemType: TabType | null) => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (itemType) params.set("item_type", itemType);
      // TOOL 含 L3 内置几十项 + 可订阅包，提高单页上限
      params.set("limit", itemType === "TOOL" ? "256" : itemType === "MCP" ? "64" : itemType === "MODEL" ? "64" : "48");
      const res = await fetch(`/api/v1/store/catalog?${params}`);
      const json = (await res.json()) as {
        success?: boolean;
        data?: CatalogItem[];
        meta?: {
          total?: number;
          hint?: string;
          hints?: string[];
          source?: string;
        };
      };
      if (json.success && Array.isArray(json.data)) {
        setItems(json.data);
        setTotal(json.meta?.total ?? json.data.length);
        const hint = json.meta?.hint;
        const hints = json.meta?.hints;
        setCatalogNote(
          hint ?? (hints?.length ? hints.join(" ") : null)
        );
      } else {
        setItems([]);
        setTotal(0);
        setCatalogNote(null);
      }
    } catch (e) {
      console.error("Failed to fetch catalog:", e);
      setItems([]);
      setTotal(0);
      setCatalogNote(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchCatalog(tab);
  }, [tab]);

  // 加载已拥有的 item_id，用于展示「已拥有」状态
  useEffect(() => {
    fetch("/api/v1/store/licenses", { credentials: "include" })
      .then((res) => res.json())
      .then((json) => {
        if (json.success && Array.isArray(json.data)) {
          setOwnedIds(new Set(json.data));
        }
      })
      .catch(() => {});
  }, []);

  const handleSubscribe = useCallback(
    async (item: CatalogItem) => {
      if (item.runtime_builtin || subscribingId) return;
      setSubscribingId(item.id);
      const installLocally = async () => {
        if (!["SKILL", "MCP", "MODEL"].includes(item.item_type)) return null;
        const pluginId = item.plugin_id || item.id;
        setToast({ message: t.toastLocalInstallStart(item.name), type: "info" });
        const res = await fetch("http://127.0.0.1:18991/api/v1/local-capability-install", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            plugin_id: pluginId,
            l1_base_url: window.location.origin,
          }),
        });
        const json = await res.json().catch(() => null);
        if (!res.ok || !json?.ok) {
          throw new Error(json?.error || `local capability install failed: HTTP ${res.status}`);
        }
        return json as { installed_count?: number; installed_path?: string; id?: string };
      };
      try {
        if (!ownedIds.has(item.id)) {
          const res = await fetch("/api/v1/store/subscribe", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ item_id: item.id }),
            credentials: "include",
          });
          const json = (await res.json()) as {
            success?: boolean;
            error?: string;
            code?: string;
          };

          if (res.status === 401) {
            router.push(`/login?redirect=${encodeURIComponent("/store")}`);
            return;
          }

          if (res.status === 400 && json.code === "ALREADY_OWNED") {
            setOwnedIds((prev) => new Set(prev).add(item.id));
          } else if (!res.ok) {
            setToast({ message: json.error || t.toastSubscribeFail, type: "error" });
            return;
          } else {
            setOwnedIds((prev) => new Set(prev).add(item.id));
          }
        }

        const local = await installLocally();
        setToast({
          message: local ? t.toastLocalInstallOk(item.name, local.installed_count ?? 1) : t.toastSubscribeOk,
          type: "success",
        });
      } catch (err) {
        console.error("Subscribe/local install error:", err);
        setToast({
          message: err instanceof Error ? t.toastLocalInstallFail(err.message) : t.toastNetwork,
          type: "error",
        });
      } finally {
        setSubscribingId(null);
      }
    },
    [subscribingId, ownedIds, router, t]
  );

  const handleInstallModel = useCallback(
    async (model: CatalogRequiredModel) => {
      if (installingModelKey) return;
      if (!model.package_url || !model.plugin_id) {
        setTab("MODEL");
        setToast({ message: t.toastModelNeedAsset, type: "info" });
        return;
      }

      const key = model.plugin_id || model.model_id;
      setInstallingModelKey(key);
      const fallbackDownload = () => {
        const anchor = document.createElement("a");
        anchor.href = `/api/v1/store/model-download?model_plugin_id=${encodeURIComponent(model.plugin_id!)}`;
        anchor.setAttribute("download", "");
        document.body.appendChild(anchor);
        anchor.click();
        document.body.removeChild(anchor);
      };
      try {
        setToast({ message: t.toastModelInstallStart(model.name), type: "info" });
        const res = await fetch("http://127.0.0.1:18991/api/v1/local-model-install", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            plugin_id: model.plugin_id,
            l1_base_url: window.location.origin,
          }),
        });
        const json = await res.json().catch(() => null);
        if (!res.ok || !json?.ok) {
          throw new Error(json?.error || `local install failed: HTTP ${res.status}`);
        }
        setToast({
          message: t.toastModelInstallOk(model.name, json.installed_path),
          type: "success",
        });
      } catch (e) {
        console.warn("Local model install unavailable, fallback to browser download:", e);
        try {
          fallbackDownload();
          setToast({ message: t.toastModelDownloadFallback(model.name), type: "info" });
        } catch (downloadError) {
          console.error("Model download error:", downloadError);
          setToast({ message: t.toastModelDownloadFail, type: "error" });
        }
      } finally {
        window.setTimeout(() => {
          setInstallingModelKey((current) => (current === key ? null : current));
        }, 1800);
      }
    },
    [installingModelKey, t]
  );

  const goModelAssetsTab = useCallback(() => {
    setTab("MODEL");
  }, []);

  return (
    <div className="min-h-screen relative overflow-hidden">
      {/* 赛博朋克网格背景 */}
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
            radial-gradient(ellipse 100% 80% at 50% 0%, rgba(34, 211, 238, 0.08) 0%, transparent 50%),
            radial-gradient(ellipse 80% 60% at 100% 80%, rgba(139, 92, 246, 0.06) 0%, transparent 50%),
            radial-gradient(ellipse 60% 50% at 0% 0%, rgba(34, 211, 238, 0.04) 0%, transparent 50%)
          `,
        }}
      />

      <Navbar />

      <main className="pt-24 pb-16 px-6 max-w-7xl mx-auto">
        <header className="mb-10">
          <h1 className="text-2xl font-bold text-white tracking-tight mb-1">
            Nexus Web Store
          </h1>
          <p className="text-white/50 text-sm font-mono">
            {t.subtitle}
          </p>
        </header>

        {catalogNote ? (
          <div
            className="mb-6 rounded-xl border border-amber-500/35 bg-amber-500/10 px-4 py-3 text-sm text-amber-100/95 leading-relaxed"
            role="status"
          >
            {catalogNote}
          </div>
        ) : null}

        {/* Tabs */}
        <div className="flex gap-2 mb-4">
          <button
            onClick={() => setTab("SKILL")}
            className={`px-5 py-2.5 rounded-lg font-medium text-sm transition-all duration-200 ${
              tab === "SKILL"
                ? "bg-cyan-500/20 text-cyan-400 border border-cyan-400/40 shadow-[0_0_20px_rgba(34,211,238,0.15)]"
                : "bg-white/5 text-white/60 border border-white/10 hover:bg-white/10 hover:text-white/80"
            }`}
          >
            {t.tabSkill}
          </button>
          <button
            onClick={() => setTab("MCP")}
            className={`px-5 py-2.5 rounded-lg font-medium text-sm transition-all duration-200 ${
              tab === "MCP"
                ? "bg-violet-500/20 text-violet-400 border border-violet-400/40 shadow-[0_0_20px_rgba(139,92,246,0.15)]"
                : "bg-white/5 text-white/60 border border-white/10 hover:bg-white/10 hover:text-white/80"
            }`}
          >
            {t.tabMcp}
          </button>
          <button
            onClick={() => setTab("TOOL")}
            className={`px-5 py-2.5 rounded-lg font-medium text-sm transition-all duration-200 ${
              tab === "TOOL"
                ? "bg-amber-500/15 text-amber-400 border border-amber-400/40 shadow-[0_0_20px_rgba(245,158,11,0.12)]"
                : "bg-white/5 text-white/60 border border-white/10 hover:bg-white/10 hover:text-white/80"
            }`}
          >
            {t.tabTool}
          </button>
          <button
            onClick={() => setTab("MODEL")}
            className={`px-5 py-2.5 rounded-lg font-medium text-sm transition-all duration-200 ${
              tab === "MODEL"
                ? "bg-emerald-500/15 text-emerald-400 border border-emerald-400/40 shadow-[0_0_20px_rgba(16,185,129,0.12)]"
                : "bg-white/5 text-white/60 border border-white/10 hover:bg-white/10 hover:text-white/80"
            }`}
          >
            模型资产
          </button>
        </div>

        {/* 商品数量 */}
        <p className="text-xs text-white/40 font-mono mb-2">
          {loading ? t.loading : t.totalItems(total)}
        </p>
        {tab === "TOOL" && !loading ? (
          <p className="text-xs text-white/45 mb-6 max-w-4xl leading-relaxed border-l border-amber-500/25 pl-3">
            {t.toolTabExplain}
          </p>
        ) : (
          <div className="mb-6" />
        )}

        {/* 商品网格 */}
        {loading ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {Array.from({ length: 8 }).map((_, i) => (
              <SkeletonCard key={i} />
            ))}
          </div>
        ) : items.length === 0 ? (
          <EmptyState tab={tab} />
        ) : (
          <motion.div
            layout
            className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4"
          >
            <AnimatePresence mode="popLayout">
              {items.map((item) => (
                <ProductCard
                  key={item.id}
                  item={item}
                  isOwned={ownedIds.has(item.id)}
                  isLoading={subscribingId === item.id}
                  installingModelKey={installingModelKey}
                  onSubscribe={handleSubscribe}
                  onInstallModel={handleInstallModel}
                  onGoModelAssetsTab={goModelAssetsTab}
                />
              ))}
            </AnimatePresence>
          </motion.div>
        )}
      </main>

      <AnimatePresence>
        {toast && (
          <Toast
            message={toast.message}
            type={toast.type}
            onDismiss={() => setToast(null)}
          />
        )}
      </AnimatePresence>
    </div>
  );
}
