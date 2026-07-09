import { useCallback, useEffect, useMemo, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import {
  AlertTriangle,
  CheckCircle2,
  Clock3,
  FolderOpen,
  Loader2,
  PackagePlus,
  RefreshCw,
  Search,
  UploadCloud,
  X,
} from "lucide-react";
import { cn } from "../../utils/cn";

type PackageStatus = "published" | "unpublished" | "update_available";

interface CapabilityPackageInfo {
  id: string;
  name: string;
  description?: string | null;
  version: string;
  kind: "mcp" | "skill" | string;
  tier: "core" | "business" | "extension" | string;
  path: string;
  manifest_path?: string | null;
  portable: boolean;
  published: boolean;
  published_version?: string | null;
  last_published_at?: string | null;
  package_path?: string | null;
  sha256_path?: string | null;
  status: PackageStatus | string;
  l1_published: boolean;
  l1_version?: string | null;
  l1_review_status?: string | null;
  l1_package_url?: string | null;
  problems: string[];
}

interface CapabilityPublishScan {
  root: string;
  state_path: string;
  output_dir: string;
  l1_direct: CapabilityL1DirectProfile;
  packages: CapabilityPackageInfo[];
  counts: Record<string, number>;
}

interface CapabilityPublishResult {
  ok: boolean;
  id: string;
  version: string;
  package_path: string;
  sha256_path?: string | null;
  published_at: string;
  uploaded_to_l1: boolean;
  l1_status?: string | null;
  dependency_results?: Array<{
    id: string;
    version: string;
    kind: string;
    package_path: string;
    uploaded_to_l1: boolean;
    l1_status?: string | null;
  }>;
  message: string;
}

interface CapabilityL1DirectProfile {
  config_path: string;
  base_url: string;
  developer_id: string;
  token_present: boolean;
  token_preview?: string | null;
  visibility: "PRIVATE" | "PUBLIC" | string;
  upload_by_default: boolean;
  l2_required: boolean;
}

interface CapabilityL1DirectTestResult {
  ok: boolean;
  base_url: string;
  developer_id?: string | null;
  catalog_reachable: boolean;
  developer_items_count?: number | null;
  message: string;
}

type FilterKey =
  | "business"
  | "all"
  | "mcp"
  | "skill"
  | "model"
  | "published"
  | "unpublished"
  | "update_available"
  | "blocked";

const BUSINESS_SKILL_ORDER = [
  "com.jachin.skill.bi-growth-officer",
  "com.jachin.skill.pmo-copilot",
  "com.jachin.skill.ai-recruiting-director",
  "com.jachin.skill.desktop-execution-agent",
  "com.jachin.skill.game-qa-automation",
  "com.jachin.skill.english-learning-assistant",
] as const;

const BUSINESS_SKILL_SET = new Set<string>(BUSINESS_SKILL_ORDER);

const FILTERS: Array<{ key: FilterKey; label: string }> = [
  { key: "business", label: "业务 Skill" },
  { key: "all", label: "全部" },
  { key: "mcp", label: "MCP" },
  { key: "skill", label: "Skill" },
  { key: "model", label: "Model" },
  { key: "published", label: "已发布" },
  { key: "unpublished", label: "未发布" },
  { key: "update_available", label: "可迭代" },
  { key: "blocked", label: "需修复" },
];

function isBusinessSkill(pkg: CapabilityPackageInfo): boolean {
  return pkg.kind === "skill" && BUSINESS_SKILL_SET.has(pkg.id);
}

function businessSkillRank(id: string): number {
  const index = BUSINESS_SKILL_ORDER.indexOf(id as (typeof BUSINESS_SKILL_ORDER)[number]);
  return index >= 0 ? index : Number.MAX_SAFE_INTEGER;
}

function bumpPatch(version: string): string {
  const m = version.match(/^(\d+)\.(\d+)\.(\d+)(.*)$/);
  if (!m) return "1.0.0";
  return `${m[1]}.${m[2]}.${Number(m[3]) + 1}${m[4] || ""}`;
}

function formatTime(raw?: string | null): string {
  if (!raw) return "未发布";
  const n = Number(raw);
  if (!Number.isNaN(n) && n > 0) {
    return new Date(n * 1000).toLocaleString();
  }
  return raw;
}

function statusLabel(status: string): string {
  if (status === "published") return "已发布";
  if (status === "update_available") return "有新版本";
  return "未发布";
}

function statusClass(status: string): string {
  if (status === "published") return "border-emerald-400/35 bg-emerald-500/10 text-emerald-200";
  if (status === "update_available") return "border-amber-400/35 bg-amber-500/10 text-amber-200";
  return "border-slate-500/35 bg-slate-500/10 text-slate-300";
}

export function CapabilityPublish() {
  const [scan, setScan] = useState<CapabilityPublishScan | null>(null);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<FilterKey>("business");
  const [versions, setVersions] = useState<Record<string, string>>({});
  const [publishing, setPublishing] = useState<string | null>(null);
  const [uploadToL1, setUploadToL1] = useState(true);
  const [l1BaseUrl, setL1BaseUrl] = useState("");
  const [l1DeveloperId, setL1DeveloperId] = useState("");
  const [l1Token, setL1Token] = useState("");
  const [visibility, setVisibility] = useState<"PRIVATE" | "PUBLIC">("PUBLIC");
  const [l1Profile, setL1Profile] = useState<CapabilityL1DirectProfile | null>(null);
  const [savingProfile, setSavingProfile] = useState(false);
  const [testingL1, setTestingL1] = useState(false);
  const [l1Test, setL1Test] = useState<CapabilityL1DirectTestResult | null>(null);
  const [notice, setNotice] = useState<{ type: "success" | "error"; text: string } | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const next = await invoke<CapabilityPublishScan>("capability_publish_scan");
      setScan(next);
      setL1Profile(next.l1_direct);
      setL1BaseUrl(next.l1_direct.base_url || "");
      setL1DeveloperId(next.l1_direct.developer_id || "");
      setVisibility(next.l1_direct.visibility === "PRIVATE" ? "PRIVATE" : "PUBLIC");
      setUploadToL1(next.l1_direct.upload_by_default || next.l1_direct.token_present);
      setVersions((prev) => {
        const merged = { ...prev };
        for (const pkg of next.packages) {
          if (!merged[pkg.id]) {
            merged[pkg.id] = pkg.status === "published" ? bumpPatch(pkg.version) : pkg.version;
          }
        }
        return merged;
      });
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

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filtered = (scan?.packages ?? []).filter((pkg) => {
      const searchable = [
        pkg.id,
        pkg.name,
        pkg.description ?? "",
        pkg.kind,
        pkg.tier,
        pkg.version,
        pkg.status,
        pkg.path,
        pkg.manifest_path ?? "",
        pkg.published_version ?? "",
        pkg.l1_version ?? "",
        pkg.l1_review_status ?? "",
        ...pkg.problems,
      ]
        .join(" ")
        .toLowerCase();
      const matchQuery = !q || searchable.includes(q);
      if (!matchQuery) return false;
      if (filter === "business") return isBusinessSkill(pkg);
      if (filter === "all") return true;
      if (filter === "mcp" || filter === "skill" || filter === "model") return pkg.kind === filter;
      if (filter === "blocked") return pkg.problems.length > 0;
      return pkg.status === filter;
    });
    return [...filtered].sort((a, b) => {
      const rankDiff = businessSkillRank(a.id) - businessSkillRank(b.id);
      if (rankDiff !== 0) return rankDiff;
      if (!q) return a.name.localeCompare(b.name);
      const score = (pkg: CapabilityPackageInfo) => {
        const id = pkg.id.toLowerCase();
        const name = pkg.name.toLowerCase();
        const desc = (pkg.description ?? "").toLowerCase();
        if (id === q || name === q) return 0;
        if (id.startsWith(q) || name.startsWith(q)) return 1;
        if (id.includes(q) || name.includes(q)) return 2;
        if (desc.includes(q)) return 3;
        return 4;
      };
      return score(a) - score(b) || a.name.localeCompare(b.name);
    });
  }, [filter, query, scan?.packages]);

  const publish = async (pkg: CapabilityPackageInfo) => {
    if (publishing) return;
    const version = (versions[pkg.id] || pkg.version).trim();
    if (!version) {
      setNotice({ type: "error", text: "版本号不能为空" });
      return;
    }
    setPublishing(pkg.id);
    try {
      const result = await invoke<CapabilityPublishResult>("capability_publish_package", {
        input: {
          path: pkg.path,
          version,
          upload_to_l1: uploadToL1,
          l1_base_url: l1BaseUrl,
          l1_token: l1Token,
          visibility,
        },
      });
      const depText =
        result.dependency_results && result.dependency_results.length > 0
          ? `；同步依赖 ${result.dependency_results.length} 个：${result.dependency_results
              .map((item) => item.id)
              .join("、")}`
          : "";
      setNotice({
        type: "success",
        text: `${result.message}：${result.id}@${result.version}${depText}`,
      });
      await load();
    } catch (e) {
      setNotice({ type: "error", text: `发布失败：${String(e)}` });
    } finally {
      setPublishing(null);
    }
  };

  const saveL1Profile = async () => {
    if (savingProfile) return;
    setSavingProfile(true);
    try {
      const profile = await invoke<CapabilityL1DirectProfile>("capability_publish_l1_direct_set", {
        input: {
          base_url: l1BaseUrl,
          developer_id: l1DeveloperId,
          token: l1Token,
          visibility,
          upload_by_default: uploadToL1,
        },
      });
      setL1Profile(profile);
      setL1Token("");
      setNotice({
        type: "success",
        text: `L1 Direct 配置已保存：${profile.base_url}`,
      });
      await load();
    } catch (e) {
      setNotice({ type: "error", text: `L1 Direct 配置保存失败：${String(e)}` });
    } finally {
      setSavingProfile(false);
    }
  };

  const testL1Direct = async () => {
    if (testingL1) return;
    setTestingL1(true);
    try {
      if (l1BaseUrl.trim()) {
        await invoke<CapabilityL1DirectProfile>("capability_publish_l1_direct_set", {
          input: {
            base_url: l1BaseUrl,
            developer_id: l1DeveloperId,
            token: l1Token,
            visibility,
            upload_by_default: uploadToL1,
          },
        });
      }
      const result = await invoke<CapabilityL1DirectTestResult>("capability_publish_l1_direct_test");
      setL1Test(result);
      setNotice({
        type: result.ok ? "success" : "error",
        text: result.message,
      });
      await load();
    } catch (e) {
      setNotice({ type: "error", text: `L1 Direct 测试失败：${String(e)}` });
    } finally {
      setTestingL1(false);
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

  const total = scan?.counts.total ?? 0;
  const published = scan?.counts.published ?? 0;
  const unpublished = scan?.counts.unpublished ?? 0;
  const updateAvailable = scan?.counts.update_available ?? 0;
  const activeQuery = query.trim();

  return (
    <div className="capability-release-page min-h-full overflow-auto text-slate-100">
      <div className="mx-auto flex max-w-[1220px] flex-col gap-5 px-5 py-5 sm:px-6 sm:py-6">
        <header className="jarvis-panel relative overflow-hidden rounded-[8px] border border-cyan-200/[0.08] bg-cyan-300/[0.018] p-5">
          <div className="jarvis-hero-grid opacity-[0.22]" aria-hidden />
          <div className="relative z-10 grid gap-5 lg:grid-cols-[minmax(0,1fr)_340px]">
            <div className="flex items-center gap-4">
              <div className="jarvis-core-stage relative hidden h-24 w-24 flex-shrink-0 items-center justify-center sm:flex">
                <svg className="jarvis-core-svg" viewBox="0 0 260 260" aria-hidden>
                  <circle className="jarvis-core-ring jarvis-core-ring-outer" cx="130" cy="130" r="108" />
                  <circle className="jarvis-core-ring jarvis-core-ring-mid" cx="130" cy="130" r="82" />
                  <circle className="jarvis-core-ring jarvis-core-ring-inner" cx="130" cy="130" r="58" />
                  <path className="jarvis-core-arc jarvis-core-arc-a" d="M130 22a108 108 0 0 1 99 65" />
                  <path className="jarvis-core-arc jarvis-core-arc-b" d="M51 204a108 108 0 0 1 0-148" />
                </svg>
                <UploadCloud className="h-7 w-7 text-cyan-100 drop-shadow-[0_0_14px_rgba(125,211,252,0.55)]" />
                <div className="jarvis-core-scan" aria-hidden />
              </div>
              <div className="min-w-0">
                <p className="mb-2 inline-flex rounded-full border border-cyan-200/[0.09] bg-cyan-300/[0.035] px-3 py-1 font-mono text-[10px] uppercase tracking-[0.18em] text-cyan-100/75">
                  L1 Capability Release
                </p>
                <h1 className="text-2xl font-semibold text-slate-100 sm:text-3xl">能力发布舱</h1>
                <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-400">
                  将 MCP、Skill、Model 打包、校验并同步到 L1。复杂信息已收进下方队列，首屏只保留关键状态。
                </p>
              </div>
            </div>

            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-1">
              <div className="jarvis-tile rounded-[8px] border border-cyan-200/[0.07] bg-slate-950/24 p-4">
                <div className="relative z-10 flex items-center justify-between">
                  <span className="text-xs text-slate-500">发布通道</span>
                  <UploadCloud className="h-4 w-4 text-cyan-100/75" />
                </div>
                <div className="relative z-10 mt-3 font-mono text-sm text-slate-100">{l1Profile?.base_url || "未配置 L1"}</div>
                <div className="relative z-10 mt-2 flex items-center gap-2 text-[11px] text-slate-500">
                  <span className={cn("h-2 w-2 rounded-full", uploadToL1 ? "bg-emerald-300 shadow-[0_0_10px_rgba(52,211,153,0.7)]" : "bg-slate-600")} />
                  {uploadToL1 ? "L1 upload armed" : "local package only"}
                </div>
              </div>
              <button
                onClick={() => void load()}
                disabled={loading}
                className="jarvis-tile relative flex min-h-[92px] items-center justify-between rounded-[8px] border border-cyan-200/[0.08] bg-cyan-300/[0.035] p-4 text-left transition hover:border-cyan-200/[0.18] hover:bg-cyan-300/[0.06] disabled:opacity-50"
              >
                <span className="relative z-10">
                  <span className="block text-sm font-medium text-cyan-50">刷新扫描</span>
                  <span className="mt-1 block text-xs text-slate-500">重新读取能力清单</span>
                </span>
                {loading ? <Loader2 className="relative z-10 h-5 w-5 animate-spin text-cyan-100" /> : <RefreshCw className="relative z-10 h-5 w-5 text-cyan-100" />}
              </button>
            </div>
          </div>
        </header>

        <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {[
            { label: "能力总线", value: total, icon: PackagePlus, hint: "packages" },
            { label: "已发布", value: published, icon: CheckCircle2, hint: "synced" },
            { label: "待发布", value: unpublished, icon: Clock3, hint: "queued" },
            { label: "可迭代", value: updateAvailable, icon: UploadCloud, hint: "upgrade" },
          ].map((item) => {
            const Icon = item.icon;
            return (
              <div key={item.label} className="jarvis-tile rounded-[8px] border border-cyan-200/[0.07] bg-cyan-300/[0.018] p-4">
                <div className="relative z-10 flex items-center justify-between">
                  <span className="text-xs text-slate-500">{item.label}</span>
                  <span className="flex h-8 w-8 items-center justify-center rounded-[7px] border border-cyan-200/[0.08] bg-cyan-300/[0.035]">
                    <Icon className="h-4 w-4 text-cyan-100/80" />
                  </span>
                </div>
                <div className="relative z-10 mt-3 flex items-end justify-between">
                  <span className="font-mono text-3xl font-semibold text-slate-100">{item.value}</span>
                  <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-slate-500">{item.hint}</span>
                </div>
              </div>
            );
          })}
        </section>

        <section className="jarvis-panel relative overflow-hidden rounded-[8px] border border-cyan-200/[0.08] bg-cyan-300/[0.018] p-4">
          <div className="relative z-10 grid gap-3 lg:grid-cols-[minmax(260px,1.15fr)_minmax(200px,0.85fr)_minmax(180px,0.75fr)_auto]">
            <label className="relative block">
              <Search className="pointer-events-none absolute left-3 top-3.5 h-4 w-4 text-cyan-100/35" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="搜索能力包、模型或路径"
                className="h-11 w-full rounded-[8px] border border-cyan-200/[0.11] bg-slate-950/45 pl-9 pr-10 text-sm text-slate-100 outline-none placeholder:text-slate-500 focus:border-cyan-200/35"
              />
              {query && (
                <button
                  type="button"
                  onClick={() => setQuery("")}
                  className="absolute right-2 top-2.5 inline-flex h-6 w-6 items-center justify-center rounded-[7px] border border-cyan-200/[0.08] text-slate-400 hover:border-cyan-200/25 hover:text-cyan-100"
                  aria-label="清空搜索"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              )}
            </label>
            <input
              value={l1BaseUrl}
              onChange={(e) => setL1BaseUrl(e.target.value)}
              placeholder="L1 地址"
              className="h-11 rounded-[8px] border border-cyan-200/[0.11] bg-slate-950/45 px-3 text-sm text-slate-100 outline-none placeholder:text-slate-500 focus:border-cyan-200/35"
            />
            <input
              value={l1DeveloperId}
              onChange={(e) => setL1DeveloperId(e.target.value)}
              placeholder="Developer ID"
              className="h-11 rounded-[8px] border border-cyan-200/[0.11] bg-slate-950/45 px-3 text-sm text-slate-100 outline-none placeholder:text-slate-500 focus:border-cyan-200/35"
            />
            <div className="flex gap-2">
              <button
                onClick={() => void saveL1Profile()}
                disabled={savingProfile}
                className="inline-flex h-11 items-center gap-2 rounded-[8px] border border-cyan-200/[0.13] bg-cyan-300/[0.055] px-3 text-sm text-cyan-50 hover:bg-cyan-300/[0.085] disabled:opacity-50"
              >
                {savingProfile ? <Loader2 className="h-4 w-4 animate-spin" /> : <UploadCloud className="h-4 w-4" />}
                保存
              </button>
              <button
                onClick={() => void testL1Direct()}
                disabled={testingL1}
                className="inline-flex h-11 items-center gap-2 rounded-[8px] border border-emerald-300/20 bg-emerald-300/[0.06] px-3 text-sm text-emerald-50 hover:bg-emerald-300/[0.09] disabled:opacity-50"
              >
                {testingL1 ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
                测试
              </button>
            </div>
          </div>

          <div className="relative z-10 mt-3 grid gap-3 lg:grid-cols-[minmax(260px,1fr)_auto]">
            <input
              value={l1Token}
              onChange={(e) => setL1Token(e.target.value)}
              placeholder={l1Profile?.token_present ? `Token 已保存：${l1Profile.token_preview ?? "***"}` : "Developer Token"}
              type="password"
              className="h-10 rounded-[8px] border border-cyan-200/[0.1] bg-slate-950/40 px-3 text-sm text-slate-100 outline-none placeholder:text-slate-500 focus:border-cyan-200/35"
            />
            <div className="flex flex-wrap gap-2">
              <select
                value={visibility}
                onChange={(e) => setVisibility(e.target.value as "PRIVATE" | "PUBLIC")}
                className="h-10 rounded-[8px] border border-cyan-200/[0.1] bg-slate-950/40 px-2 text-sm text-slate-100 outline-none"
              >
                <option value="PRIVATE">PRIVATE</option>
                <option value="PUBLIC">PUBLIC</option>
              </select>
              <label className="inline-flex h-10 items-center gap-2 rounded-[8px] border border-cyan-200/[0.1] bg-slate-950/36 px-3 text-sm text-slate-300">
                <input type="checkbox" checked={uploadToL1} onChange={(e) => setUploadToL1(e.target.checked)} className="h-4 w-4 accent-cyan-400" />
                上传 L1
              </label>
            </div>
          </div>
          <div className="relative z-10 mt-4 flex flex-wrap gap-2">
            {FILTERS.map((item) => (
              <button
                key={item.key}
                onClick={() => setFilter(item.key)}
                className={cn(
                  "h-8 rounded-[7px] border px-3 text-xs transition-colors",
                  filter === item.key
                    ? "border-cyan-200/[0.18] bg-cyan-300/[0.075] text-cyan-50"
                    : "border-cyan-200/[0.07] bg-slate-950/25 text-slate-500 hover:border-cyan-200/[0.16] hover:text-slate-200"
                )}
              >
                {item.label}
              </button>
            ))}
          </div>

          <div className="relative z-10 mt-4 flex flex-wrap items-center gap-2 font-mono text-[10px] uppercase tracking-[0.1em] text-slate-500">
            <span className="rounded-full border border-cyan-200/[0.07] bg-cyan-300/[0.02] px-2.5 py-1">{rows.length}/{total} visible</span>
            <span className="rounded-full border border-cyan-200/[0.07] bg-cyan-300/[0.02] px-2.5 py-1">L2 required false</span>
            {activeQuery && <span className="rounded-full border border-cyan-200/[0.07] bg-cyan-300/[0.02] px-2.5 py-1">query {activeQuery}</span>}
            {l1Test && <span className={cn("rounded-full border px-2.5 py-1", l1Test.ok ? "border-emerald-300/20 text-emerald-200" : "border-rose-300/20 text-rose-200")}>{l1Test.message}</span>}
          </div>
        </section>

        {notice && (
          <div
            className={cn(
              "rounded-[8px] border px-4 py-3 text-sm",
              notice.type === "success"
                ? "border-emerald-300/20 bg-emerald-300/[0.06] text-emerald-100"
                : "border-rose-300/20 bg-rose-300/[0.06] text-rose-100"
            )}
          >
            {notice.text}
          </div>
        )}

        <section className="min-h-[360px]">
          {loading ? (
            <div className="jarvis-panel flex items-center gap-3 rounded-[8px] border border-cyan-200/[0.08] bg-cyan-300/[0.018] px-4 py-12 text-slate-400">
              <Loader2 className="h-5 w-5 animate-spin text-cyan-300" />
              正在扫描能力包
            </div>
          ) : rows.length === 0 ? (
            <div className="jarvis-panel rounded-[8px] border border-cyan-200/[0.08] bg-cyan-300/[0.018] px-4 py-12 text-sm text-slate-500">
              没有匹配的能力包。
              {activeQuery && (
                <button onClick={() => setQuery("")} className="ml-3 rounded-[7px] border border-cyan-200/[0.1] px-2 py-1 text-slate-300 hover:border-cyan-200/25 hover:text-cyan-100">
                  清空搜索
                </button>
              )}
            </div>
          ) : (
            <div className="grid gap-4 lg:grid-cols-2">
              {rows.map((pkg) => {
                const isBusy = publishing === pkg.id;
                const blocked = pkg.problems.length > 0;
                return (
                  <article key={`${pkg.id}:${pkg.path}`} className="jarvis-tile relative overflow-hidden rounded-[8px] border border-cyan-200/[0.075] bg-cyan-300/[0.018] p-4">
                    <div className="relative z-10 flex items-start justify-between gap-4">
                      <div className="min-w-0">
                        <div className="flex min-w-0 items-center gap-2">
                          <span className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-[7px] border border-cyan-200/[0.09] bg-cyan-300/[0.035] text-cyan-100/90">
                            <PackagePlus className="h-4 w-4" />
                          </span>
                          <div className="min-w-0">
                            <h2 className="truncate text-sm font-semibold text-slate-100" title={pkg.id}>{pkg.name || pkg.id}</h2>
                            <p className="mt-0.5 truncate font-mono text-[10px] text-cyan-100/55" title={pkg.id}>{pkg.id}</p>
                          </div>
                          {blocked && <AlertTriangle className="h-4 w-4 flex-shrink-0 text-amber-300" />}
                        </div>
                        {pkg.description && <p className="mt-3 line-clamp-2 text-xs leading-5 text-slate-400">{pkg.description}</p>}
                      </div>
                      <span className={cn("flex-shrink-0 rounded-full border px-2.5 py-1 text-[11px]", statusClass(pkg.status))}>{statusLabel(pkg.status)}</span>
                    </div>

                    <div className="relative z-10 mt-4 grid gap-2 sm:grid-cols-4">
                      <InfoPill label="类型" value={pkg.kind} />
                      <InfoPill label="分层" value={pkg.tier} />
                      <InfoPill label="当前" value={pkg.version} />
                      <InfoPill label="L1" value={pkg.l1_published ? pkg.l1_version || "online" : "local"} />
                    </div>

                    <div className="relative z-10 mt-4 grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto]">
                      <div>
                        <label className="mb-1 block font-mono text-[10px] uppercase tracking-[0.12em] text-slate-500">New Version</label>
                        <input
                          value={versions[pkg.id] ?? pkg.version}
                          onChange={(e) => setVersions((prev) => ({ ...prev, [pkg.id]: e.target.value }))}
                          className="h-10 w-full rounded-[8px] border border-cyan-200/[0.1] bg-slate-950/45 px-3 font-mono text-sm text-slate-100 outline-none focus:border-cyan-200/35"
                        />
                      </div>
                      <div className="flex items-end gap-2">
                        {pkg.l1_published && (
                          <button
                            onClick={() => setVersions((prev) => ({ ...prev, [pkg.id]: bumpPatch(pkg.version) }))}
                            className="h-10 rounded-[8px] border border-cyan-200/[0.09] bg-slate-950/30 px-3 text-xs text-cyan-100/75 hover:border-cyan-200/22 hover:text-cyan-50"
                          >
                            +patch
                          </button>
                        )}
                        <button
                          onClick={() => void publish(pkg)}
                          disabled={isBusy || blocked}
                          className="inline-flex h-10 items-center justify-center gap-2 rounded-[8px] border border-cyan-200/[0.14] bg-cyan-300/[0.07] px-4 text-sm font-medium text-cyan-50 hover:bg-cyan-300/[0.11] disabled:cursor-not-allowed disabled:opacity-45"
                        >
                          {isBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <UploadCloud className="h-4 w-4" />}
                          {isBusinessSkill(pkg) ? "发布业务包" : "发布"}
                        </button>
                      </div>
                    </div>

                    <div className="relative z-10 mt-4 flex flex-wrap items-center gap-2 border-t border-cyan-200/[0.055] pt-3 text-xs text-slate-500">
                      <button onClick={() => void openPath(pkg.path)} className="inline-flex min-w-0 max-w-full items-center gap-1.5 hover:text-cyan-100" title={pkg.path}>
                        <FolderOpen className="h-3.5 w-3.5 flex-shrink-0" />
                        <span className="truncate">{pkg.path}</span>
                      </button>
                      <button
                        onClick={() => void openPath(pkg.package_path)}
                        disabled={!pkg.package_path}
                        className="ml-auto inline-flex h-8 items-center gap-1.5 rounded-[7px] border border-cyan-200/[0.08] bg-slate-950/24 px-2.5 text-slate-400 hover:border-cyan-200/20 hover:text-cyan-100 disabled:cursor-not-allowed disabled:opacity-35"
                      >
                        <FolderOpen className="h-3.5 w-3.5" />
                        产物
                      </button>
                    </div>

                    {blocked && (
                      <div className="relative z-10 mt-3 rounded-[8px] border border-amber-300/20 bg-amber-300/[0.055] px-3 py-2 text-xs text-amber-100">
                        {pkg.problems.join("；")}
                      </div>
                    )}
                  </article>
                );
              })}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

function InfoPill({ label, value }: { label: string; value?: string | null }) {
  return (
    <div className="rounded-[8px] border border-cyan-200/[0.065] bg-slate-950/24 px-3 py-2">
      <div className="font-mono text-[9px] uppercase tracking-[0.12em] text-slate-500">{label}</div>
      <div className="mt-1 truncate font-mono text-xs text-slate-200" title={value ?? "-"}>
        {value || "-"}
      </div>
    </div>
  );
}
