import { useCallback, useEffect, useMemo, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import {
  AlertTriangle,
  CheckCircle2,
  DownloadCloud,
  FolderOpen,
  Loader2,
  Power,
  RefreshCw,
  Search,
  Trash2,
  Wrench,
} from "lucide-react";
import { cn } from "../../utils/cn";
import { INVENTORY_UPDATED_EVENT } from "../../hooks/useUISyncEventSource";

type InstallStatus =
  | "installed"
  | "not_installed"
  | "update_available"
  | "repair_needed"
  | "disabled"
  | "local_only"
  | "source_cached"
  | "source_mismatch"
  | "blocked";

interface CapabilityInstallItem {
  id: string;
  name: string;
  kind: "mcp" | "skill" | "model" | string;
  description?: string | null;
  l1_version?: string | null;
  local_version?: string | null;
  package_url?: string | null;
  package_sha256?: string | null;
  installed_sha256?: string | null;
  installed_path?: string | null;
  source_store_path?: string | null;
  enabled: boolean;
  source: string;
  source_l1_base_url?: string | null;
  source_l1_profile_id?: string | null;
  current_l1_match: boolean;
  current_l1_cached: boolean;
  l1_status?: string | null;
  status: InstallStatus | string;
  problems: string[];
  dependencies: string[];
}

interface CapabilityInstallScan {
  l1_base_url: string;
  active_l1_profile_id?: string | null;
  registry_path: string;
  mcp_cache_dir: string;
  skill_cache_dir: string;
  model_cache_dir: string;
  source_store_dir: string;
  download_dir: string;
  items: CapabilityInstallItem[];
  counts: Record<string, number>;
}

interface CapabilityL1Profile {
  id: string;
  name: string;
  base_url: string;
  developer_id?: string | null;
  token_present: boolean;
  token_preview?: string | null;
  active: boolean;
}

interface CapabilityL1ProfilesResult {
  active_profile_id?: string | null;
  profiles: CapabilityL1Profile[];
  config_path: string;
}

interface CapabilityInstallResult {
  ok: boolean;
  id: string;
  version: string;
  kind: string;
  installed_path: string;
  package_sha256: string;
  message: string;
}

type FilterKey = "all" | InstallStatus | "mcp" | "skill" | "model";

const FILTERS: Array<{ key: FilterKey; label: string }> = [
  { key: "all", label: "全部" },
  { key: "not_installed", label: "未安装" },
  { key: "update_available", label: "可更新" },
  { key: "installed", label: "已安装" },
  { key: "repair_needed", label: "需修复" },
  { key: "source_cached", label: "已缓存" },
  { key: "source_mismatch", label: "其他 L1" },
  { key: "disabled", label: "已禁用" },
  { key: "local_only", label: "本地包" },
  { key: "mcp", label: "MCP" },
  { key: "skill", label: "Skill" },
  { key: "model", label: "Model" },
];

const INSTALLABLE_STATUSES = new Set([
  "not_installed",
  "update_available",
  "repair_needed",
  "source_cached",
  "source_mismatch",
]);
const READY_STATUSES = new Set(["installed", "local_only"]);

function statusLabel(status: string): string {
  if (status === "installed") return "已安装";
  if (status === "not_installed") return "未安装";
  if (status === "update_available") return "可更新";
  if (status === "repair_needed") return "需修复";
  if (status === "source_cached") return "当前 L1 已缓存";
  if (status === "source_mismatch") return "其他 L1 来源";
  if (status === "disabled") return "已禁用";
  if (status === "local_only") return "本地开发包";
  return "不可安装";
}

function statusClass(status: string): string {
  if (status === "installed") return "border-emerald-400/35 bg-emerald-500/10 text-emerald-200";
  if (status === "update_available") return "border-amber-400/35 bg-amber-500/10 text-amber-200";
  if (status === "repair_needed") return "border-rose-400/35 bg-rose-500/10 text-rose-200";
  if (status === "source_cached") return "border-cyan-400/35 bg-cyan-500/10 text-cyan-100";
  if (status === "source_mismatch") return "border-sky-400/35 bg-sky-500/10 text-sky-100";
  if (status === "disabled") return "border-slate-500/35 bg-slate-500/10 text-slate-300";
  if (status === "local_only") return "border-violet-400/35 bg-violet-500/10 text-violet-200";
  if (status === "blocked") return "border-red-400/35 bg-red-500/10 text-red-200";
  return "border-cyan-400/35 bg-cyan-500/10 text-cyan-100";
}

function shortSha(raw?: string | null): string {
  if (!raw) return "-";
  return raw.length > 14 ? `${raw.slice(0, 8)}...${raw.slice(-6)}` : raw;
}

function normalizeDependencyId(raw: string): string {
  return raw.trim().replace(/^(model|mcp):/i, "");
}

function notifyInventoryUpdated(detail: Record<string, unknown>) {
  window.dispatchEvent(new CustomEvent(INVENTORY_UPDATED_EVENT, { detail }));
}

export function CapabilityInstallCenter() {
  const [scan, setScan] = useState<CapabilityInstallScan | null>(null);
  const [profiles, setProfiles] = useState<CapabilityL1ProfilesResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<FilterKey>("all");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ type: "success" | "error"; text: string } | null>(null);
  const [profileBusy, setProfileBusy] = useState(false);
  const [newL1Name, setNewL1Name] = useState("");
  const [newL1BaseUrl, setNewL1BaseUrl] = useState("");
  const [newL1DeveloperId, setNewL1DeveloperId] = useState("");
  const [newL1Token, setNewL1Token] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [next, nextProfiles] = await Promise.all([
        invoke<CapabilityInstallScan>("capability_install_scan"),
        invoke<CapabilityL1ProfilesResult>("capability_l1_profiles_get"),
      ]);
      setScan(next);
      setProfiles(nextProfiles);
      setNotice(null);
    } catch (e) {
      setNotice({ type: "error", text: `扫描失败：${String(e)}` });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const activeProfile = useMemo(
    () => profiles?.profiles.find((profile) => profile.active) ?? profiles?.profiles[0] ?? null,
    [profiles?.profiles]
  );

  const itemById = useMemo(() => {
    const map = new Map<string, CapabilityInstallItem>();
    for (const item of scan?.items ?? []) {
      map.set(item.id, item);
    }
    return map;
  }, [scan?.items]);

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (scan?.items ?? []).filter((item) => {
      const matchQuery =
        !q ||
        item.id.toLowerCase().includes(q) ||
        item.name.toLowerCase().includes(q) ||
        (item.source_l1_base_url ?? "").toLowerCase().includes(q) ||
        (item.source_store_path ?? "").toLowerCase().includes(q) ||
        (item.installed_path ?? "").toLowerCase().includes(q);
      if (!matchQuery) return false;
      if (filter === "all") return true;
      if (filter === "mcp" || filter === "skill" || filter === "model") return item.kind === filter;
      return item.status === filter;
    });
  }, [filter, query, scan?.items]);

  const installOne = async (item: CapabilityInstallItem, repair = false) => {
    if (item.problems.length > 0) {
      throw new Error(`${item.id} 暂不可安装：${item.problems.join("；")}`);
    }
    return invoke<CapabilityInstallResult>("capability_install_package", {
      input: {
        id: item.id,
        package_url: item.package_url,
        kind: item.kind,
        repair,
      },
    });
  };

  const install = async (item: CapabilityInstallItem) => {
    if (busyId) return;
    setBusyId(item.id);
    try {
      const deps = item.dependencies
        .map(normalizeDependencyId)
        .map((id) => itemById.get(id))
        .filter((dep): dep is CapabilityInstallItem => Boolean(dep))
        .filter((dep) => !READY_STATUSES.has(dep.status) || !dep.enabled);

      for (let i = 0; i < deps.length; i += 1) {
        const dep = deps[i];
        setNotice({
          type: "success",
          text: `正在安装依赖 ${i + 1}/${deps.length}：${dep.name || dep.id}`,
        });
        await installOne(dep, dep.status === "repair_needed");
      }

      setNotice({ type: "success", text: `正在安装：${item.name || item.id}` });
      const res = await installOne(item, item.status === "repair_needed");
      const suffix = deps.length > 0 ? `，并已安装 ${deps.length} 个依赖` : "";
      setNotice({ type: "success", text: `${res.id}@${res.version} 安装完成${suffix}` });
      await load();
      notifyInventoryUpdated({ type: "CAPABILITY_INSTALLED", id: res.id, kind: res.kind, version: res.version });
    } catch (e) {
      setNotice({ type: "error", text: `安装失败：${String(e)}` });
    } finally {
      setBusyId(null);
    }
  };

  const activateProfile = async (id: string) => {
    if (profileBusy) return;
    setProfileBusy(true);
    try {
      const next = await invoke<CapabilityL1ProfilesResult>("capability_l1_profile_activate", {
        input: { id },
      });
      setProfiles(next);
      setNotice({ type: "success", text: "L1 已切换，正在重新对账当前 catalog" });
      await load();
      notifyInventoryUpdated({ type: "L1_PROFILE_SWITCHED", id });
    } catch (e) {
      setNotice({ type: "error", text: `切换 L1 失败：${String(e)}` });
    } finally {
      setProfileBusy(false);
    }
  };

  const saveProfile = async () => {
    if (profileBusy) return;
    setProfileBusy(true);
    try {
      const next = await invoke<CapabilityL1ProfilesResult>("capability_l1_profile_save", {
        input: {
          name: newL1Name,
          base_url: newL1BaseUrl,
          developer_id: newL1DeveloperId,
          developer_token: newL1Token,
          activate: true,
        },
      });
      setProfiles(next);
      setNewL1Name("");
      setNewL1BaseUrl("");
      setNewL1DeveloperId("");
      setNewL1Token("");
      setNotice({ type: "success", text: "L1 Profile 已保存并切换，正在重新对账" });
      await load();
      notifyInventoryUpdated({ type: "L1_PROFILE_SAVED" });
    } catch (e) {
      setNotice({ type: "error", text: `保存 L1 Profile 失败：${String(e)}` });
    } finally {
      setProfileBusy(false);
    }
  };

  const setEnabled = async (item: CapabilityInstallItem, enabled: boolean) => {
    if (busyId) return;
    setBusyId(item.id);
    try {
      await invoke<CapabilityInstallResult>("capability_install_set_enabled", {
        input: { id: item.id, enabled },
      });
      setNotice({ type: "success", text: `${item.id} 已${enabled ? "启用" : "禁用"}` });
      await load();
      notifyInventoryUpdated({ type: enabled ? "CAPABILITY_ENABLED" : "CAPABILITY_DISABLED", id: item.id, kind: item.kind });
    } catch (e) {
      setNotice({ type: "error", text: `切换失败：${String(e)}` });
    } finally {
      setBusyId(null);
    }
  };

  const uninstall = async (item: CapabilityInstallItem) => {
    if (busyId) return;
    const ok = window.confirm(`确认卸载 ${item.id}？这会删除本机缓存目录，但不会影响 L1。`);
    if (!ok) return;
    setBusyId(item.id);
    try {
      await invoke("capability_install_uninstall", {
        input: { id: item.id, confirm: true },
      });
      setNotice({ type: "success", text: `${item.id} 已卸载` });
      await load();
      notifyInventoryUpdated({ type: "CAPABILITY_UNINSTALLED", id: item.id, kind: item.kind });
    } catch (e) {
      setNotice({ type: "error", text: `卸载失败：${String(e)}` });
    } finally {
      setBusyId(null);
    }
  };

  const openPath = async (path?: string | null) => {
    if (!path) return;
    try {
      await invoke("capability_publish_open_path", { path });
    } catch (e) {
      setNotice({ type: "error", text: `打开失败：${String(e)}` });
    }
  };

  const count = (key: string) => scan?.counts[key] ?? 0;

  return (
    <div className="min-h-full overflow-auto bg-slate-950 text-slate-100">
      <div className="mx-auto flex max-w-[1500px] flex-col gap-5 px-6 py-6">
        <header className="flex flex-wrap items-start justify-between gap-4 border-b border-cyan-500/15 pb-5">
          <div>
            <p className="text-xs uppercase tracking-[0.32em] text-cyan-300/70">L3 Capability Install Center</p>
            <h1 className="mt-2 text-2xl font-semibold text-white">MCP / Skill 安装中心</h1>
            <p className="mt-2 max-w-3xl text-sm text-slate-400">
              L3 直连 L1 catalog，并和本机 installed registry 对账，判断哪些能力已安装、未安装、可更新或需要修复。
            </p>
          </div>
          <button
            onClick={() => void load()}
            disabled={loading}
            className="inline-flex h-10 items-center gap-2 rounded-md border border-cyan-400/30 bg-cyan-500/10 px-4 text-sm text-cyan-100 hover:bg-cyan-500/15 disabled:opacity-50"
          >
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            刷新对账
          </button>
        </header>

        <section className="grid gap-3 md:grid-cols-5">
          {[
            { label: "L1 能力", value: count("total"), icon: DownloadCloud, tone: "text-cyan-200" },
            { label: "已安装", value: count("installed"), icon: CheckCircle2, tone: "text-emerald-200" },
            { label: "未安装", value: count("not_installed"), icon: DownloadCloud, tone: "text-slate-200" },
            { label: "可更新", value: count("update_available"), icon: RefreshCw, tone: "text-amber-200" },
            { label: "需修复", value: count("repair_needed"), icon: Wrench, tone: "text-rose-200" },
          ].map((item) => {
            const Icon = item.icon;
            return (
              <div key={item.label} className="rounded-md border border-cyan-500/15 bg-slate-900/65 p-4">
                <div className="flex items-center justify-between">
                  <span className="text-sm text-slate-400">{item.label}</span>
                  <Icon className={cn("h-5 w-5", item.tone)} />
                </div>
                <div className="mt-3 text-3xl font-semibold text-white">{item.value}</div>
              </div>
            );
          })}
        </section>

        <section className="rounded-md border border-cyan-500/15 bg-slate-900/55 p-4">
          <div className="flex flex-wrap items-end gap-3">
            <label className="min-w-[260px] flex-1 text-xs text-slate-400">
              当前 L1
              <select
                value={activeProfile?.id ?? scan?.active_l1_profile_id ?? ""}
                onChange={(e) => void activateProfile(e.target.value)}
                disabled={profileBusy || !profiles?.profiles.length}
                className="mt-2 h-10 w-full rounded-md border border-slate-700 bg-slate-950/70 px-3 text-sm text-slate-100 outline-none focus:border-cyan-400/60"
              >
                {(profiles?.profiles ?? []).map((profile) => (
                  <option key={profile.id} value={profile.id}>
                    {profile.name} · {profile.base_url}
                  </option>
                ))}
              </select>
            </label>
            <label className="min-w-[180px] flex-1 text-xs text-slate-400">
              新 L1 名称
              <input
                value={newL1Name}
                onChange={(e) => setNewL1Name(e.target.value)}
                placeholder="Cloud L1 / Local L1"
                className="mt-2 h-10 w-full rounded-md border border-slate-700 bg-slate-950/70 px-3 text-sm text-slate-100 outline-none focus:border-cyan-400/60"
              />
            </label>
            <label className="min-w-[280px] flex-[1.5] text-xs text-slate-400">
              新 L1 地址
              <input
                value={newL1BaseUrl}
                onChange={(e) => setNewL1BaseUrl(e.target.value)}
                placeholder="https://nexus.example.com"
                className="mt-2 h-10 w-full rounded-md border border-slate-700 bg-slate-950/70 px-3 text-sm text-slate-100 outline-none focus:border-cyan-400/60"
              />
            </label>
            <label className="min-w-[220px] flex-1 text-xs text-slate-400">
              Developer ID
              <input
                value={newL1DeveloperId}
                onChange={(e) => setNewL1DeveloperId(e.target.value)}
                placeholder={activeProfile?.developer_id ?? "可选"}
                className="mt-2 h-10 w-full rounded-md border border-slate-700 bg-slate-950/70 px-3 text-sm text-slate-100 outline-none focus:border-cyan-400/60"
              />
            </label>
            <label className="min-w-[220px] flex-1 text-xs text-slate-400">
              Developer Token
              <input
                value={newL1Token}
                onChange={(e) => setNewL1Token(e.target.value)}
                placeholder={activeProfile?.token_present ? `已保存：${activeProfile.token_preview ?? "***"}` : "可选"}
                className="mt-2 h-10 w-full rounded-md border border-slate-700 bg-slate-950/70 px-3 text-sm text-slate-100 outline-none focus:border-cyan-400/60"
              />
            </label>
            <button
              onClick={() => void saveProfile()}
              disabled={profileBusy || !newL1BaseUrl.trim()}
              className="inline-flex h-10 items-center gap-2 rounded-md border border-cyan-400/30 bg-cyan-500/10 px-4 text-sm text-cyan-100 hover:bg-cyan-500/15 disabled:opacity-50"
            >
              {profileBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
              保存并切换
            </button>
          </div>
          <p className="mt-3 text-xs text-slate-500">
            当前：{activeProfile?.base_url || scan?.l1_base_url || "未配置"}；配置：
            {profiles?.config_path || "~/.jachin/l1_profiles.json"}。切换 L1 后只按当前 catalog 对账，本机其他来源能力会保留。
          </p>
        </section>

        <section className="rounded-md border border-cyan-500/15 bg-slate-900/55 p-4">
          <label className="relative block">
            <Search className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-slate-500" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="搜索 id、名称或安装路径"
              className="h-10 w-full rounded-md border border-slate-700 bg-slate-950/70 pl-9 pr-3 text-sm text-slate-100 outline-none focus:border-cyan-400/60"
            />
          </label>
          <div className="mt-3 flex flex-wrap gap-2">
            {FILTERS.map((item) => (
              <button
                key={item.key}
                onClick={() => setFilter(item.key)}
                className={cn(
                  "h-8 rounded-md border px-3 text-xs transition-colors",
                  filter === item.key
                    ? "border-cyan-400/45 bg-cyan-500/15 text-cyan-100"
                    : "border-slate-700 bg-slate-950/35 text-slate-400 hover:border-cyan-500/30 hover:text-cyan-200"
                )}
              >
                {item.label}
              </button>
            ))}
          </div>
          {scan && (
            <p className="mt-3 text-xs text-slate-500">
              L1：{scan.l1_base_url || "未配置"}；registry：{scan.registry_path}；MCP：{scan.mcp_cache_dir}；Skill：
              {scan.skill_cache_dir}；Model：{scan.model_cache_dir}；Source Store：{scan.source_store_dir}
            </p>
          )}
        </section>

        {notice && (
          <div
            className={cn(
              "rounded-md border px-4 py-3 text-sm",
              notice.type === "success"
                ? "border-emerald-400/30 bg-emerald-500/10 text-emerald-100"
                : "border-rose-400/30 bg-rose-500/10 text-rose-100"
            )}
          >
            {notice.text}
          </div>
        )}

        <section className="overflow-hidden rounded-md border border-cyan-500/15 bg-slate-900/55">
          <div className="grid grid-cols-[minmax(320px,1.6fr)_95px_130px_130px_150px_160px_220px] border-b border-cyan-500/15 px-4 py-3 text-xs uppercase tracking-wider text-slate-500">
            <div>能力</div>
            <div>类型</div>
            <div>L1 版本</div>
            <div>本地版本</div>
            <div>状态</div>
            <div>SHA256</div>
            <div>操作</div>
          </div>
          {loading ? (
            <div className="flex items-center gap-3 px-4 py-12 text-slate-400">
              <Loader2 className="h-5 w-5 animate-spin text-cyan-300" />
              正在对账 L1 与本机安装状态
            </div>
          ) : rows.length === 0 ? (
            <div className="px-4 py-12 text-sm text-slate-500">没有匹配的 MCP、Skill 或模型资产。</div>
          ) : (
            <div className="divide-y divide-slate-800/90">
              {rows.map((item) => {
                const busy = busyId === item.id;
                const canInstall = INSTALLABLE_STATUSES.has(item.status);
                const pendingDeps = item.dependencies
                  .map(normalizeDependencyId)
                  .map((id) => itemById.get(id))
                  .filter((dep): dep is CapabilityInstallItem => Boolean(dep))
                  .filter((dep) => !READY_STATUSES.has(dep.status) || !dep.enabled);
                return (
                  <div
                    key={`${item.id}:${item.source}`}
                    className="grid grid-cols-[minmax(320px,1.6fr)_95px_130px_130px_150px_160px_220px] gap-0 px-4 py-4 text-sm"
                  >
                    <div className="min-w-0 pr-4">
                      <div className="flex min-w-0 items-center gap-2">
                        <span className="truncate font-medium text-slate-100" title={item.id}>
                          {item.name || item.id}
                        </span>
                        {item.problems.length > 0 && <AlertTriangle className="h-4 w-4 flex-shrink-0 text-amber-300" />}
                      </div>
                      <div className="mt-1 truncate font-mono text-xs text-cyan-200/70" title={item.id}>
                        {item.id}
                      </div>
                      {item.installed_path && (
                        <button
                          onClick={() => void openPath(item.installed_path)}
                          className="mt-2 inline-flex max-w-full items-center gap-1 text-left text-xs text-slate-500 hover:text-cyan-200"
                          title={item.installed_path}
                        >
                          <FolderOpen className="h-3.5 w-3.5 flex-shrink-0" />
                          <span className="truncate">{item.installed_path}</span>
                        </button>
                      )}
                      {item.source_store_path && (
                        <button
                          onClick={() => void openPath(item.source_store_path)}
                          className="mt-1 inline-flex max-w-full items-center gap-1 text-left text-xs text-sky-300/65 hover:text-sky-100"
                          title={item.source_store_path}
                        >
                          <FolderOpen className="h-3.5 w-3.5 flex-shrink-0" />
                          <span className="truncate">源包：{item.source_store_path}</span>
                        </button>
                      )}
                      {item.problems.length > 0 && (
                        <div className="mt-2 rounded border border-amber-400/20 bg-amber-500/5 px-2 py-1 text-xs text-amber-100">
                          {item.problems.join("；")}
                        </div>
                      )}
                      {item.dependencies?.length > 0 && (
                        <div className="mt-2 rounded border border-cyan-400/15 bg-cyan-500/5 px-2 py-1 text-xs text-cyan-100/80">
                          依赖：{item.dependencies.map(normalizeDependencyId).join("、")}
                        </div>
                      )}
                    </div>
                    <div>
                      <span className="rounded border border-slate-600/60 px-2 py-1 text-xs uppercase text-slate-300">
                        {item.kind}
                      </span>
                    </div>
                    <div className="font-mono text-slate-200">{item.l1_version ?? "-"}</div>
                    <div className="font-mono text-slate-200">{item.local_version ?? "-"}</div>
                    <div>
                      <span className={cn("rounded border px-2 py-1 text-xs", statusClass(item.status))}>
                        {statusLabel(item.status)}
                      </span>
                      {item.l1_status && <div className="mt-2 text-xs text-slate-500">L1 {item.l1_status}</div>}
                      {item.source_l1_base_url && !item.current_l1_match && (
                        <div className="mt-2 text-xs text-sky-200/70" title={item.source_l1_base_url}>
                          来源：{item.source_l1_base_url}
                        </div>
                      )}
                    </div>
                    <div className="space-y-1 font-mono text-xs text-slate-500">
                      <div title={item.package_sha256 ?? ""}>L1 {shortSha(item.package_sha256)}</div>
                      <div title={item.installed_sha256 ?? ""}>本地 {shortSha(item.installed_sha256)}</div>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {canInstall && (
                        <button
                          onClick={() => void install(item)}
                          disabled={busy || item.problems.length > 0}
                          className="inline-flex h-8 items-center gap-1.5 rounded-md border border-cyan-400/30 bg-cyan-500/10 px-2.5 text-xs text-cyan-100 hover:bg-cyan-500/15 disabled:opacity-45"
                        >
                          {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <DownloadCloud className="h-3.5 w-3.5" />}
                          {item.kind === "model"
                            ? item.status === "update_available"
                              ? "更新模型"
                              : item.status === "repair_needed"
                                ? "修复模型"
                                : item.status === "source_cached"
                                  ? "启用模型"
                                : item.status === "source_mismatch"
                                  ? "切换来源"
                                  : "下载模型"
                            : item.status === "update_available"
                              ? "更新"
                              : item.status === "repair_needed"
                                ? "修复"
                                : item.status === "source_cached"
                                  ? "启用来源"
                                : item.status === "source_mismatch"
                                  ? "切换来源安装"
                                : pendingDeps.length > 0
                                  ? `安装全部(${pendingDeps.length + 1})`
                                  : "安装"}
                        </button>
                      )}
                      {item.installed_path && (
                        <button
                          onClick={() => void setEnabled(item, !item.enabled)}
                          disabled={busy}
                          className="inline-flex h-8 items-center gap-1.5 rounded-md border border-slate-700 bg-slate-950/30 px-2.5 text-xs text-slate-300 hover:border-cyan-500/30 hover:text-cyan-100 disabled:opacity-45"
                        >
                          <Power className="h-3.5 w-3.5" />
                          {item.enabled ? "禁用" : "启用"}
                        </button>
                      )}
                      {item.installed_path && (
                        <button
                          onClick={() => void uninstall(item)}
                          disabled={busy}
                          className="inline-flex h-8 items-center gap-1.5 rounded-md border border-rose-400/25 bg-rose-500/10 px-2.5 text-xs text-rose-100 hover:bg-rose-500/15 disabled:opacity-45"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                          卸载
                        </button>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
