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
    <div className="min-h-full overflow-auto bg-slate-950 text-slate-100">
      <div className="mx-auto flex max-w-[1500px] flex-col gap-5 px-6 py-6">
        <header className="flex flex-wrap items-start justify-between gap-4 border-b border-cyan-500/15 pb-5">
          <div>
            <p className="text-xs uppercase tracking-[0.32em] text-cyan-300/70">L1 Capability Release</p>
            <h1 className="mt-2 text-2xl font-semibold text-white">MCP / Skill / Model 发布工作台</h1>
            <p className="mt-2 max-w-3xl text-sm text-slate-400">
              自动检测项目中的能力包，区分已发布、未发布和可迭代版本；发布业务 Skill 时会同步发布它依赖的 MCP 和 Model，生成 L1 ZIP 包、SHA256 校验文件，并记录本地发布状态。
            </p>
          </div>
          <button
            onClick={() => void load()}
            disabled={loading}
            className="inline-flex h-10 items-center gap-2 rounded-md border border-cyan-400/30 bg-cyan-500/10 px-4 text-sm text-cyan-100 hover:bg-cyan-500/15 disabled:opacity-50"
          >
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            刷新扫描
          </button>
        </header>

        <section className="grid gap-3 md:grid-cols-4">
          {[
            { label: "能力包", value: total, icon: PackagePlus, tone: "text-cyan-200" },
            { label: "已发布", value: published, icon: CheckCircle2, tone: "text-emerald-200" },
            { label: "未发布", value: unpublished, icon: Clock3, tone: "text-slate-200" },
            { label: "可迭代", value: updateAvailable, icon: UploadCloud, tone: "text-amber-200" },
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
          <div className="grid gap-3 lg:grid-cols-[1.2fr_1fr_1fr_auto]">
            <label className="relative block">
              <Search className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-slate-500" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="搜索 Skill/MCP/Model：英语、english、id、名称、路径、描述"
                className="h-10 w-full rounded-md border border-slate-700 bg-slate-950/70 pl-9 pr-10 text-sm text-slate-100 outline-none focus:border-cyan-400/60"
              />
              {query && (
                <button
                  type="button"
                  onClick={() => setQuery("")}
                  className="absolute right-2 top-2 inline-flex h-6 w-6 items-center justify-center rounded border border-slate-700 text-slate-400 hover:border-cyan-500/40 hover:text-cyan-100"
                  aria-label="清空搜索"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              )}
            </label>
            <input
              value={l1BaseUrl}
              onChange={(e) => setL1BaseUrl(e.target.value)}
              placeholder="L1 地址，可选，例如 https://nexus.example.com"
              className="h-10 rounded-md border border-slate-700 bg-slate-950/70 px-3 text-sm text-slate-100 outline-none focus:border-cyan-400/60"
            />
            <input
              value={l1DeveloperId}
              onChange={(e) => setL1DeveloperId(e.target.value)}
              placeholder="Developer ID，用于读取已发布列表"
              className="h-10 rounded-md border border-slate-700 bg-slate-950/70 px-3 text-sm text-slate-100 outline-none focus:border-cyan-400/60"
            />
            <div className="flex items-center gap-2">
              <button
                onClick={() => void saveL1Profile()}
                disabled={savingProfile}
                className="inline-flex h-10 items-center gap-2 rounded-md border border-cyan-400/30 bg-cyan-500/10 px-3 text-sm text-cyan-100 hover:bg-cyan-500/15 disabled:opacity-50"
              >
                {savingProfile ? <Loader2 className="h-4 w-4 animate-spin" /> : <UploadCloud className="h-4 w-4" />}
                保存
              </button>
              <button
                onClick={() => void testL1Direct()}
                disabled={testingL1}
                className="inline-flex h-10 items-center gap-2 rounded-md border border-emerald-400/25 bg-emerald-500/10 px-3 text-sm text-emerald-100 hover:bg-emerald-500/15 disabled:opacity-50"
              >
                {testingL1 ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}
                测试
              </button>
            </div>
          </div>
          <div className="mt-3 grid gap-3 lg:grid-cols-[1fr_auto_auto]">
            <input
              value={l1Token}
              onChange={(e) => setL1Token(e.target.value)}
              placeholder={l1Profile?.token_present ? `Developer Token 已保存：${l1Profile.token_preview ?? "***"}` : "Developer Token"}
              type="password"
              className="h-10 rounded-md border border-slate-700 bg-slate-950/70 px-3 text-sm text-slate-100 outline-none focus:border-cyan-400/60"
            />
            <div className="flex items-center gap-2">
              <select
                value={visibility}
                onChange={(e) => setVisibility(e.target.value as "PRIVATE" | "PUBLIC")}
                className="h-10 rounded-md border border-slate-700 bg-slate-950/70 px-2 text-sm text-slate-100 outline-none"
              >
                <option value="PRIVATE">PRIVATE</option>
                <option value="PUBLIC">PUBLIC</option>
              </select>
              <label className="inline-flex h-10 items-center gap-2 rounded-md border border-slate-700 bg-slate-950/50 px-3 text-sm text-slate-300">
                <input
                  type="checkbox"
                  checked={uploadToL1}
                  onChange={(e) => setUploadToL1(e.target.checked)}
                  className="h-4 w-4 accent-cyan-400"
                />
                上传 L1
              </label>
            </div>
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-3 text-xs text-slate-500">
            <span>L3 → L1 Direct：{l1Profile?.base_url || "未配置"}</span>
            <span>L2 required：false</span>
            {l1Test && (
              <span className={l1Test.ok ? "text-emerald-300" : "text-rose-300"}>
                {l1Test.message}
                {typeof l1Test.developer_items_count === "number" ? `；远端包 ${l1Test.developer_items_count} 个` : ""}
              </span>
            )}
          </div>
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
          <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-slate-500">
            <span>
              当前显示 {rows.length} / {total} 个能力包
              {activeQuery ? `，搜索：${activeQuery}` : ""}
            </span>
            {activeQuery && (
              <button
                onClick={() => setQuery("")}
                className="rounded border border-slate-700 px-2 py-1 text-slate-300 hover:border-cyan-500/40 hover:text-cyan-100"
              >
                清空搜索
              </button>
            )}
          </div>
          {scan && (
            <p className="mt-3 text-xs text-slate-500">
              根目录：{scan.root}；发布记录：{scan.state_path}；输出目录：{scan.output_dir}
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
          <div className="grid grid-cols-[minmax(320px,1.5fr)_120px_120px_140px_190px_210px_180px] border-b border-cyan-500/15 px-4 py-3 text-xs uppercase tracking-wider text-slate-500">
            <div>能力包</div>
            <div>类型</div>
            <div>分层</div>
            <div>当前版本</div>
            <div>发布状态</div>
            <div>新版本</div>
            <div>操作</div>
          </div>
          {loading ? (
            <div className="flex items-center gap-3 px-4 py-12 text-slate-400">
              <Loader2 className="h-5 w-5 animate-spin text-cyan-300" />
              正在扫描能力包
            </div>
          ) : rows.length === 0 ? (
            <div className="px-4 py-12 text-sm text-slate-500">
              没有匹配的 MCP、Skill 或 Model 包。
              {activeQuery && (
                <button
                  onClick={() => setQuery("")}
                  className="ml-3 rounded border border-slate-700 px-2 py-1 text-slate-300 hover:border-cyan-500/40 hover:text-cyan-100"
                >
                  清空搜索
                </button>
              )}
            </div>
          ) : (
            <div className="divide-y divide-slate-800/90">
              {rows.map((pkg) => {
                const isBusy = publishing === pkg.id;
                const blocked = pkg.problems.length > 0;
                return (
                  <div
                    key={`${pkg.id}:${pkg.path}`}
                    className="grid grid-cols-[minmax(320px,1.5fr)_120px_120px_140px_190px_210px_180px] gap-0 px-4 py-4 text-sm"
                  >
                    <div className="min-w-0 pr-4">
                      <div className="flex min-w-0 items-center gap-2">
                        <span className="truncate font-medium text-slate-100" title={pkg.id}>
                          {pkg.name || pkg.id}
                        </span>
                        {blocked && <AlertTriangle className="h-4 w-4 flex-shrink-0 text-amber-300" />}
                      </div>
                      <div className="mt-1 truncate font-mono text-xs text-cyan-200/70" title={pkg.id}>
                        {pkg.id}
                      </div>
                      {pkg.description && (
                        <div className="mt-1 line-clamp-2 text-xs leading-5 text-slate-400" title={pkg.description}>
                          {pkg.description}
                        </div>
                      )}
                      <button
                        onClick={() => void openPath(pkg.path)}
                        className="mt-2 inline-flex max-w-full items-center gap-1 text-left text-xs text-slate-500 hover:text-cyan-200"
                        title={pkg.path}
                      >
                        <FolderOpen className="h-3.5 w-3.5 flex-shrink-0" />
                        <span className="truncate">{pkg.path}</span>
                      </button>
                      {blocked && (
                        <div className="mt-2 rounded border border-amber-400/20 bg-amber-500/5 px-2 py-1 text-xs text-amber-100">
                          {pkg.problems.join("；")}
                        </div>
                      )}
                    </div>
                    <div className="flex items-start">
                      <span className="rounded border border-slate-600/60 px-2 py-1 text-xs uppercase text-slate-300">
                        {pkg.kind}
                      </span>
                    </div>
                    <div className="flex items-start">
                      <span className="rounded border border-cyan-500/20 bg-cyan-500/5 px-2 py-1 text-xs text-cyan-100">
                        {pkg.tier}
                      </span>
                    </div>
                    <div className="font-mono text-slate-200">{pkg.version}</div>
                    <div>
                      <span className={cn("rounded border px-2 py-1 text-xs", statusClass(pkg.status))}>
                        {statusLabel(pkg.status)}
                      </span>
                      <div className="mt-2 text-xs text-slate-500">
                        {pkg.published_version ? `已发 ${pkg.published_version}` : "无发布记录"}
                      </div>
                      {pkg.l1_published && (
                        <div className="mt-1 text-xs text-cyan-300/80">
                          L1 {pkg.l1_version}
                          {pkg.l1_review_status ? ` / ${pkg.l1_review_status}` : ""}
                        </div>
                      )}
                      <div className="mt-1 text-xs text-slate-600">{formatTime(pkg.last_published_at)}</div>
                    </div>
                    <div className="pr-3">
                      <input
                        value={versions[pkg.id] ?? pkg.version}
                        onChange={(e) => setVersions((prev) => ({ ...prev, [pkg.id]: e.target.value }))}
                        className="h-9 w-full rounded-md border border-slate-700 bg-slate-950/70 px-2 font-mono text-sm text-slate-100 outline-none focus:border-cyan-400/60"
                      />
                      {(pkg.published || pkg.l1_published) && (
                        <button
                          onClick={() => setVersions((prev) => ({ ...prev, [pkg.id]: bumpPatch(pkg.version) }))}
                          className="mt-2 text-xs text-cyan-300 hover:text-cyan-100"
                        >
                          自动递增 patch
                        </button>
                      )}
                    </div>
                    <div className="flex flex-col gap-2">
                      <button
                        onClick={() => void publish(pkg)}
                        disabled={isBusy || blocked}
                        className="inline-flex h-9 items-center justify-center gap-2 rounded-md border border-cyan-400/30 bg-cyan-500/10 px-3 text-xs font-medium text-cyan-100 hover:bg-cyan-500/15 disabled:cursor-not-allowed disabled:opacity-45"
                      >
                        {isBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <UploadCloud className="h-4 w-4" />}
                        {isBusinessSkill(pkg)
                          ? pkg.published || pkg.l1_published
                            ? "发布业务包"
                            : "一键发布"
                          : pkg.published || pkg.l1_published
                            ? "发布新版本"
                            : "发布"}
                      </button>
                      <button
                        onClick={() => void openPath(pkg.package_path)}
                        disabled={!pkg.package_path}
                        className="inline-flex h-8 items-center justify-center gap-2 rounded-md border border-slate-700 bg-slate-950/30 px-3 text-xs text-slate-300 hover:border-cyan-500/30 hover:text-cyan-100 disabled:cursor-not-allowed disabled:opacity-35"
                      >
                        <FolderOpen className="h-3.5 w-3.5" />
                        打开产物
                      </button>
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
