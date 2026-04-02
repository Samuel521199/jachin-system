"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useSession } from "next-auth/react";
import {
  Loader2,
  Users,
  Building2,
  Cpu,
  Shield,
  PlusCircle,
  UserPlus,
  Link2,
  Copy,
  Check,
} from "lucide-react";
import ConsoleScaffold from "@/components/ConsoleScaffold";
import type { OrgRole } from "@/lib/org-constants";
import {
  ORG_ROLES_ALL,
  ORG_ROLES_CAN_INVITE,
  ORG_ROLES_INVITABLE,
} from "@/lib/org-constants";
import {
  ORG_ROLE_DESCRIPTIONS,
  formatDeviceGroupRole,
  formatOrgRole,
} from "@/lib/org-role-ui";

type OrgRow = {
  org_id: string;
  name: string;
  role: string;
  is_personal_default: boolean;
};

type MemberRow = {
  user_id: string;
  role: string;
  email: string | null;
  name: string | null;
  joined_at: string | null;
};

type DeviceGroupRow = {
  id: string;
  name: string;
  description: string | null;
  agent_count: number;
  my_group_role: string | null;
};

const INVITE_TTL_PRESETS: { label: string; sec: number }[] = [
  { label: "15 分钟", sec: 900 },
  { label: "1 小时", sec: 3600 },
  { label: "24 小时", sec: 86400 },
  { label: "7 天", sec: 7 * 86400 },
];

export default function ConsoleWorkspacePage() {
  const router = useRouter();
  const { data: session, status, update } = useSession();
  const [orgs, setOrgs] = useState<OrgRow[]>([]);
  const [activeOrgId, setActiveOrgId] = useState<string | null>(null);
  const [members, setMembers] = useState<MemberRow[]>([]);
  const [groups, setGroups] = useState<DeviceGroupRow[]>([]);
  const [loadingOrgs, setLoadingOrgs] = useState(true);
  const [loadingMembers, setLoadingMembers] = useState(false);
  const [loadingGroups, setLoadingGroups] = useState(false);
  const [switching, setSwitching] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  const [createName, setCreateName] = useState("");
  const [createBusy, setCreateBusy] = useState(false);

  const [joinToken, setJoinToken] = useState("");
  const [joinBusy, setJoinBusy] = useState(false);
  const [joinedOrgId, setJoinedOrgId] = useState<string | null>(null);

  const [inviteRole, setInviteRole] = useState<string>("member");
  const [inviteTtlSec, setInviteTtlSec] = useState(900);
  const [inviteBusy, setInviteBusy] = useState(false);
  const [inviteTokenOut, setInviteTokenOut] = useState<string | null>(null);
  const [copied, setCopied] = useState<"token" | "link" | null>(null);

  const loadOrgs = useCallback(async () => {
    setLoadingOrgs(true);
    setError(null);
    try {
      const res = await fetch("/api/v1/organizations/list", {
        credentials: "same-origin",
      });
      const json = await res.json();
      if (!res.ok) {
        setError(json.message ?? json.error ?? "无法加载组织列表");
        setOrgs([]);
        return;
      }
      const data = json.data;
      setActiveOrgId(data?.active_org_id ?? null);
      setOrgs(data?.organizations ?? []);
    } catch {
      setError("网络错误，无法加载工作区");
      setOrgs([]);
    } finally {
      setLoadingOrgs(false);
    }
  }, []);

  const loadMembersAndGroups = useCallback(async () => {
    setLoadingMembers(true);
    setLoadingGroups(true);
    try {
      const [mRes, gRes] = await Promise.all([
        fetch("/api/v1/organizations/members", { credentials: "same-origin" }),
        fetch("/api/v1/organizations/device-groups", {
          credentials: "same-origin",
        }),
      ]);
      const mJson = await mRes.json();
      const gJson = await gRes.json();
      if (mRes.ok && mJson.success) {
        setMembers(mJson.data?.members ?? []);
      } else {
        setMembers([]);
      }
      if (gRes.ok && gJson.success) {
        setGroups(gJson.data?.groups ?? []);
      } else {
        setGroups([]);
      }
    } finally {
      setLoadingMembers(false);
      setLoadingGroups(false);
    }
  }, []);

  useEffect(() => {
    if (status !== "authenticated") return;
    void loadOrgs();
  }, [status, loadOrgs]);

  useEffect(() => {
    if (status !== "authenticated") return;
    void loadMembersAndGroups();
  }, [status, session?.user?.orgId, loadMembersAndGroups]);

  /** 打开邀请链接时从 #invite= 或 ?invite= 预填 Token（不经过服务器日志） */
  useEffect(() => {
    if (status !== "authenticated" || typeof window === "undefined") return;
    let token = "";
    const hash = window.location.hash.replace(/^#/, "");
    if (hash) {
      const hp = new URLSearchParams(hash);
      const h = hp.get("invite");
      if (h) token = h;
    }
    if (!token) {
      const sp = new URLSearchParams(window.location.search);
      const q = sp.get("invite");
      if (q) token = q;
    }
    if (token) {
      setJoinToken(token);
      setSuccessMsg("已从邀请链接填入 Token，确认后点击下方「加入工作区」。");
      window.history.replaceState(
        null,
        "",
        `${window.location.pathname}`
      );
    }
  }, [status]);

  const handleCreateWorkspace = async () => {
    const name = createName.trim();
    if (!name || createBusy) return;
    setCreateBusy(true);
    setError(null);
    setSuccessMsg(null);
    setJoinedOrgId(null);
    try {
      const res = await fetch("/api/v1/organizations/create", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ name }),
      });
      const json = await res.json();
      if (!res.ok || !json.success) {
        setError(json.message ?? "创建工作区失败");
        return;
      }
      const oid = json.data?.org_id as string;
      setCreateName("");
      setSuccessMsg(`已创建工作区「${json.data?.name ?? name}」。可切换过去或继续邀请成员。`);
      await loadOrgs();
      if (oid) {
        await handleSwitchOrg(oid);
      }
    } catch {
      setError("创建工作区失败");
    } finally {
      setCreateBusy(false);
    }
  };

  const handleJoinWorkspace = async () => {
    const token = joinToken.trim();
    if (!token || joinBusy) return;
    setJoinBusy(true);
    setError(null);
    setSuccessMsg(null);
    setJoinedOrgId(null);
    try {
      const res = await fetch("/api/v1/organizations/members/join", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ token }),
      });
      const json = await res.json();
      if (!res.ok || !json.success) {
        setError(json.message ?? "加入失败，请检查 Token 是否过期或已使用");
        return;
      }
      const oid = json.data?.org_id as string;
      setJoinedOrgId(oid ?? null);
      setSuccessMsg(
        json.data?.already_member
          ? "你已是该工作区成员。"
          : "已成功加入工作区。可点击下方按钮切换上下文，或稍后在列表中切换。"
      );
      setJoinToken("");
      await loadOrgs();
    } catch {
      setError("加入工作区失败");
    } finally {
      setJoinBusy(false);
    }
  };

  const handleGenerateInvite = async () => {
    if (inviteBusy) return;
    setInviteBusy(true);
    setError(null);
    setInviteTokenOut(null);
    try {
      const res = await fetch("/api/v1/organizations/members/invite", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({
          role: inviteRole,
          expires_in_sec: inviteTtlSec,
        }),
      });
      const json = await res.json();
      if (!res.ok || !json.success) {
        setError(json.message ?? "无法生成邀请（需为所有者或管理员，且当前会话在工作区内）");
        return;
      }
      setInviteTokenOut(json.data?.token ?? null);
      setSuccessMsg("邀请已生成，请将 Token 或链接发给对方；对方需登录后在「加入工作区」中粘贴或打开链接。");
    } catch {
      setError("生成邀请失败");
    } finally {
      setInviteBusy(false);
    }
  };

  const copyText = async (text: string, kind: "token" | "link") => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(kind);
      window.setTimeout(() => setCopied(null), 2000);
    } catch {
      setError("复制失败，请手动选择文本复制");
    }
  };

  const handleSwitchOrg = async (orgId: string) => {
    if (!orgId || switching) return;
    setSwitching(orgId);
    setError(null);
    try {
      const res = await fetch("/api/v1/organizations/active-org", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ org_id: orgId }),
      });
      const json = await res.json();
      if (!res.ok) {
        setError(json.message ?? "切换工作区失败");
        return;
      }
      setActiveOrgId(orgId);
      if (typeof update === "function") {
        await update();
      }
      router.refresh();
      await loadMembersAndGroups();
      await loadOrgs();
    } catch {
      setError("切换工作区失败");
    } finally {
      setSwitching(null);
    }
  };

  if (status === "loading") {
    return (
      <ConsoleScaffold>
        <main className="pt-24 px-6 pb-16 flex justify-center">
          <Loader2 className="w-10 h-10 text-cyan-400 animate-spin" />
        </main>
      </ConsoleScaffold>
    );
  }

  if (status !== "authenticated" || !session?.user) {
    return (
      <ConsoleScaffold>
        <main className="pt-24 px-6 pb-16 max-w-3xl mx-auto text-center">
          <p className="text-white/60 mb-4">请先登录以查看工作区与权限。</p>
          <Link
            href="/login?callbackUrl=/console/workspace"
            className="text-cyan-400 hover:text-cyan-300"
          >
            去登录
          </Link>
        </main>
      </ConsoleScaffold>
    );
  }

  const u = session.user;
  const currentOrg = orgs.find((o) => o.org_id === (activeOrgId ?? u.orgId));
  const sessionOrgRole = u.orgRole ?? "";
  const canInvite = (ORG_ROLES_CAN_INVITE as readonly string[]).includes(
    sessionOrgRole
  );
  const inviteLink =
    typeof window !== "undefined" && inviteTokenOut
      ? `${window.location.origin}/console/workspace#invite=${encodeURIComponent(inviteTokenOut)}`
      : inviteTokenOut
        ? `/console/workspace#invite=${encodeURIComponent(inviteTokenOut)}`
        : "";

  return (
    <ConsoleScaffold>
      <main className="pt-24 px-6 pb-16 max-w-5xl mx-auto">
        <div className="flex flex-wrap items-end justify-between gap-4 mb-10">
          <div>
            <h1 className="text-2xl font-bold tracking-tight text-cyan-400/95">
              工作区与权限
            </h1>
            <p className="text-sm text-white/50 mt-1">
              租户边界来自组织（工作区）；会话内 <code className="text-cyan-400/80">org_id</code>{" "}
              决定商店同步、舰队与 API 数据范围。边缘设备（L3）向 <strong className="text-white/70">L2</strong>{" "}
              配对时须填写与当前 L2 绑定的同一 <code className="text-cyan-400/80">organization_id</code>
              （可在下方列表复制）；可用{" "}
              <code className="text-white/50 text-xs">GET /api/v1/me/workspaces</code> 供设备端下拉。
            </p>
          </div>
          <Link
            href="/console"
            className="text-sm text-white/50 hover:text-cyan-400 transition-colors"
          >
            返回指挥台
          </Link>
        </div>

        {error ? (
          <div className="mb-6 rounded-xl border border-amber-500/30 bg-amber-950/20 px-4 py-3 text-sm text-amber-200/90">
            {error}
          </div>
        ) : null}

        {successMsg ? (
          <div className="mb-6 rounded-xl border border-emerald-500/30 bg-emerald-950/20 px-4 py-3 text-sm text-emerald-100/90">
            {successMsg}
          </div>
        ) : null}

        {!loadingOrgs && orgs.length === 0 ? (
          <div className="mb-6 rounded-xl border border-cyan-500/35 bg-cyan-950/25 px-4 py-4 text-sm text-cyan-100/90">
            <p className="font-medium text-cyan-300/95 mb-1">请先创建或加入工作区</p>
            <p className="text-white/55 text-xs leading-relaxed">
              新账号注册后不会自动拥有组织。请在本页创建团队工作区，或通过邀请加入；完成后即可使用商店、舰队、以及使用 L1 邮箱登录 L2 网关（须为工作区所有者或管理员）。
            </p>
          </div>
        ) : null}

        {/* 创建 / 加入 / 邀请 */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
          <section className="rounded-2xl border border-white/10 bg-white/[0.02] p-6">
            <h2 className="text-sm font-semibold uppercase tracking-widest text-cyan-400/90 flex items-center gap-2 mb-3">
              <PlusCircle className="w-4 h-4" />
              创建团队工作区
            </h2>
            <p className="text-xs text-white/45 mb-4">
              新建独立租户，你将成为所有者。注册流程不再自动创建「个人工作区」，此处为首选入口。
            </p>
            <div className="flex flex-col sm:flex-row gap-2">
              <input
                type="text"
                value={createName}
                onChange={(e) => setCreateName(e.target.value)}
                placeholder="工作区显示名称"
                maxLength={128}
                className="flex-1 rounded-lg bg-black/40 border border-white/15 px-3 py-2 text-sm text-white placeholder:text-white/30 focus:border-cyan-500/50 focus:outline-none"
              />
              <button
                type="button"
                disabled={createBusy || !createName.trim()}
                onClick={() => void handleCreateWorkspace()}
                className="shrink-0 px-4 py-2 rounded-lg bg-cyan-500/20 border border-cyan-500/45 text-cyan-300 text-sm font-medium hover:bg-cyan-500/30 disabled:opacity-40 disabled:pointer-events-none"
              >
                {createBusy ? (
                  <Loader2 className="w-4 h-4 animate-spin inline" />
                ) : (
                  "创建并切换"
                )}
              </button>
            </div>
          </section>

          <section className="rounded-2xl border border-white/10 bg-white/[0.02] p-6">
            <h2 className="text-sm font-semibold uppercase tracking-widest text-violet-400/90 flex items-center gap-2 mb-3">
              <UserPlus className="w-4 h-4" />
              通过邀请加入工作区
            </h2>
            <p className="text-xs text-white/45 mb-4">
              向工作区管理员索取邀请 Token，或打开对方发来的邀请链接（将自动填入）。加入后需切换工作区方可访问该租户数据。
            </p>
            <textarea
              value={joinToken}
              onChange={(e) => setJoinToken(e.target.value)}
              placeholder="粘贴邀请 JWT…"
              rows={3}
              className="w-full rounded-lg bg-black/40 border border-white/15 px-3 py-2 text-xs font-mono text-white/90 placeholder:text-white/30 focus:border-violet-500/50 focus:outline-none resize-y min-h-[80px]"
            />
            <div className="mt-3 flex flex-wrap gap-2">
              <button
                type="button"
                disabled={joinBusy || !joinToken.trim()}
                onClick={() => void handleJoinWorkspace()}
                className="px-4 py-2 rounded-lg bg-violet-500/20 border border-violet-500/45 text-violet-200 text-sm font-medium hover:bg-violet-500/30 disabled:opacity-40 disabled:pointer-events-none"
              >
                {joinBusy ? (
                  <Loader2 className="w-4 h-4 animate-spin inline" />
                ) : (
                  "加入工作区"
                )}
              </button>
              {joinedOrgId ? (
                <button
                  type="button"
                  disabled={!!switching}
                  onClick={() => void handleSwitchOrg(joinedOrgId)}
                  className="px-4 py-2 rounded-lg bg-white/10 border border-white/20 text-white/90 text-sm hover:bg-white/15 disabled:opacity-40"
                >
                  切换到刚加入的工作区
                </button>
              ) : null}
            </div>
          </section>
        </div>

        {canInvite ? (
          <section className="rounded-2xl border border-cyan-500/20 bg-cyan-950/10 p-6 mb-6">
            <h2 className="text-sm font-semibold uppercase tracking-widest text-cyan-400/90 flex items-center gap-2 mb-3">
              <Link2 className="w-4 h-4" />
              邀请他人加入当前工作区
            </h2>
            <p className="text-xs text-white/45 mb-4">
              基于当前会话工作区{" "}
              <span className="text-cyan-400/80 font-medium">
                {currentOrg?.name || u.orgId || "（未知）"}
              </span>
              签发短效邀请。对方须登录本站的账号后使用 Token 或链接加入；无法通过邀请成为所有者。
            </p>
            <div className="flex flex-wrap items-end gap-3 mb-4">
              <div>
                <label className="block text-xs text-white/40 mb-1">加入后的角色</label>
                <select
                  value={inviteRole}
                  onChange={(e) => setInviteRole(e.target.value)}
                  className="rounded-lg bg-black/40 border border-white/15 px-3 py-2 text-sm text-white focus:border-cyan-500/50 focus:outline-none"
                >
                  {ORG_ROLES_INVITABLE.map((r) => (
                    <option key={r} value={r}>
                      {r}（{formatOrgRole(r)}）
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-xs text-white/40 mb-1">有效期</label>
                <select
                  value={inviteTtlSec}
                  onChange={(e) => setInviteTtlSec(Number(e.target.value))}
                  className="rounded-lg bg-black/40 border border-white/15 px-3 py-2 text-sm text-white focus:border-cyan-500/50 focus:outline-none"
                >
                  {INVITE_TTL_PRESETS.map((p) => (
                    <option key={p.sec} value={p.sec}>
                      {p.label}
                    </option>
                  ))}
                </select>
              </div>
              <button
                type="button"
                disabled={inviteBusy}
                onClick={() => void handleGenerateInvite()}
                className="px-4 py-2 rounded-lg bg-cyan-500/25 border border-cyan-500/50 text-cyan-200 text-sm font-medium hover:bg-cyan-500/35 disabled:opacity-40"
              >
                {inviteBusy ? (
                  <Loader2 className="w-4 h-4 animate-spin inline" />
                ) : (
                  "生成邀请"
                )}
              </button>
            </div>
            {inviteTokenOut ? (
              <div className="space-y-3 rounded-xl border border-white/10 bg-black/30 p-4">
                <p className="text-xs text-white/50">邀请 Token（整段复制给对方）</p>
                <textarea
                  readOnly
                  value={inviteTokenOut}
                  rows={4}
                  className="w-full rounded-lg bg-black/50 border border-white/10 px-2 py-2 text-[11px] font-mono text-white/80"
                />
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => void copyText(inviteTokenOut, "token")}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white/10 text-xs text-white/90 hover:bg-white/15"
                  >
                    {copied === "token" ? (
                      <Check className="w-3.5 h-3.5 text-emerald-400" />
                    ) : (
                      <Copy className="w-3.5 h-3.5" />
                    )}
                    复制 Token
                  </button>
                  <button
                    type="button"
                    onClick={() => void copyText(inviteLink, "link")}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-white/10 text-xs text-white/90 hover:bg-white/15"
                  >
                    {copied === "link" ? (
                      <Check className="w-3.5 h-3.5 text-emerald-400" />
                    ) : (
                      <Copy className="w-3.5 h-3.5" />
                    )}
                    复制邀请链接
                  </button>
                </div>
                <p className="text-[11px] text-white/35">
                  链接使用 URL 哈希携带 Token，不会发送到服务器访问日志；若链接过长，请改用复制 Token。
                </p>
              </div>
            ) : null}
          </section>
        ) : (
          <section className="rounded-2xl border border-white/10 bg-white/[0.02] p-4 mb-6 text-xs text-white/45">
            你在当前工作区内的角色为「{formatOrgRole(sessionOrgRole)}」，无权发放邀请；需所有者或管理员操作。
          </section>
        )}

        {/* 账号 */}
        <section className="rounded-2xl border border-white/10 bg-white/[0.02] p-6 mb-6">
          <h2 className="text-sm font-semibold uppercase tracking-widest text-violet-400/90 flex items-center gap-2 mb-4">
            <Shield className="w-4 h-4" />
            当前账号
          </h2>
          <dl className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-sm">
            <div>
              <dt className="text-white/40 mb-1">用户 ID</dt>
              <dd className="font-mono text-white/90 break-all">{u.id}</dd>
            </div>
            <div>
              <dt className="text-white/40 mb-1">邮箱 / 名称</dt>
              <dd className="text-white/90">
                {u.email ?? "—"}
                {u.name ? (
                  <span className="text-white/50"> · {u.name}</span>
                ) : null}
              </dd>
            </div>
            <div>
              <dt className="text-white/40 mb-1">会话内工作区 ID</dt>
              <dd className="font-mono text-cyan-400/90 text-xs break-all">
                {u.orgId || "—"}
              </dd>
            </div>
            <div>
              <dt className="text-white/40 mb-1">在当前工作区内的组织角色</dt>
              <dd className="text-white/90">
                <span className="text-cyan-400/90 font-medium">
                  {formatOrgRole(sessionOrgRole)}
                </span>
                <span className="text-white/40 text-xs ml-2 font-mono">
                  ({sessionOrgRole || "—"})
                </span>
              </dd>
            </div>
          </dl>
        </section>

        {/* 工作区列表 */}
        <section className="rounded-2xl border border-white/10 bg-white/[0.02] p-6 mb-6">
          <h2 className="text-sm font-semibold uppercase tracking-widest text-cyan-400/90 flex items-center gap-2 mb-4">
            <Building2 className="w-4 h-4" />
            工作区（组织）
          </h2>
          {loadingOrgs ? (
            <div className="flex items-center gap-2 text-white/50 text-sm">
              <Loader2 className="w-4 h-4 animate-spin" />
              加载中…
            </div>
          ) : orgs.length === 0 ? (
            <p className="text-white/50 text-sm">暂无组织数据（请确认已配置数据库并完成注册）。</p>
          ) : (
            <ul className="space-y-3">
              {orgs.map((o) => {
                const isActive =
                  o.org_id === (activeOrgId ?? u.orgId) ||
                  (!activeOrgId && o.org_id === u.orgId);
                return (
                  <li
                    key={o.org_id}
                    className={`flex flex-wrap items-center justify-between gap-3 rounded-xl border px-4 py-3 ${
                      isActive
                        ? "border-cyan-500/40 bg-cyan-950/20"
                        : "border-white/10 bg-black/20"
                    }`}
                  >
                    <div className="min-w-0">
                      <p className="font-medium text-white/95 truncate">{o.name}</p>
                      <p className="text-xs text-white/45 font-mono truncate mt-0.5">
                        {o.org_id}
                      </p>
                      <p className="text-xs text-white/50 mt-1">
                        我在此工作区：
                        <span className="text-cyan-400/80">
                          {formatOrgRole(o.role)}
                        </span>
                        {o.is_personal_default ? (
                          <span className="ml-2 text-violet-400/70">· 个人默认工作区</span>
                        ) : null}
                      </p>
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      {isActive ? (
                        <span className="text-xs px-2 py-1 rounded-md bg-cyan-500/20 text-cyan-300 border border-cyan-500/30">
                          当前
                        </span>
                      ) : (
                        <button
                          type="button"
                          disabled={!!switching}
                          onClick={() => void handleSwitchOrg(o.org_id)}
                          className="text-xs px-3 py-1.5 rounded-lg bg-white/10 text-white/90 hover:bg-white/15 border border-white/15 disabled:opacity-50"
                        >
                          {switching === o.org_id ? (
                            <Loader2 className="w-3 h-3 animate-spin inline" />
                          ) : (
                            "切换到此工作区"
                          )}
                        </button>
                      )}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
          {currentOrg ? (
            <p className="text-xs text-white/40 mt-4">
              当前展示的成员与设备组均属于「{currentOrg.name}」。
            </p>
          ) : null}
        </section>

        {/* 成员 */}
        <section className="rounded-2xl border border-white/10 bg-white/[0.02] p-6 mb-6">
          <h2 className="text-sm font-semibold uppercase tracking-widest text-emerald-400/90 flex items-center gap-2 mb-4">
            <Users className="w-4 h-4" />
            当前工作区成员
          </h2>
          {loadingMembers ? (
            <div className="flex items-center gap-2 text-white/50 text-sm">
              <Loader2 className="w-4 h-4 animate-spin" />
              加载中…
            </div>
          ) : members.length === 0 ? (
            <p className="text-white/50 text-sm">暂无成员或无权查看（需登录且会话含 org）。</p>
          ) : (
            <div className="overflow-x-auto rounded-xl border border-white/10">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-white/10 text-left text-xs text-white/45 uppercase tracking-wider">
                    <th className="py-3 px-3">成员</th>
                    <th className="py-3 px-3">组织角色</th>
                    <th className="py-3 px-3">加入时间</th>
                  </tr>
                </thead>
                <tbody>
                  {members.map((m) => (
                    <tr
                      key={m.user_id}
                      className="border-b border-white/5 hover:bg-white/[0.02]"
                    >
                      <td className="py-3 px-3">
                        <span className="text-white/90">{m.name || m.email || m.user_id}</span>
                        {m.email ? (
                          <span className="block text-xs text-white/40 font-mono">
                            {m.email}
                          </span>
                        ) : null}
                      </td>
                      <td className="py-3 px-3">
                        <span className="text-cyan-400/85">{formatOrgRole(m.role)}</span>
                        <span className="text-white/35 text-xs ml-1 font-mono">
                          ({m.role})
                        </span>
                      </td>
                      <td className="py-3 px-3 text-white/50 text-xs">
                        {m.joined_at
                          ? new Date(m.joined_at).toLocaleString()
                          : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {/* 设备组 */}
        <section className="rounded-2xl border border-white/10 bg-white/[0.02] p-6 mb-6">
          <h2 className="text-sm font-semibold uppercase tracking-widest text-amber-400/90 flex items-center gap-2 mb-4">
            <Cpu className="w-4 h-4" />
            设备组（车队 / 站点）
          </h2>
          <p className="text-xs text-white/45 mb-4">
            组级权限在租户角色之下做细粒度覆写；未列入组时，以组织角色为准。
          </p>
          {loadingGroups ? (
            <div className="flex items-center gap-2 text-white/50 text-sm">
              <Loader2 className="w-4 h-4 animate-spin" />
              加载中…
            </div>
          ) : groups.length === 0 ? (
            <p className="text-white/50 text-sm">
              当前工作区下尚无设备组，或设备尚未归属到组。可在数据层创建{" "}
              <code className="text-white/60">device_groups</code> 后在此查看。
            </p>
          ) : (
            <ul className="space-y-2">
              {groups.map((g) => (
                <li
                  key={g.id}
                  className="rounded-xl border border-white/10 bg-black/20 px-4 py-3 flex flex-wrap justify-between gap-2"
                >
                  <div>
                    <p className="font-medium text-white/90">{g.name}</p>
                    {g.description ? (
                      <p className="text-xs text-white/45 mt-0.5">{g.description}</p>
                    ) : null}
                    <p className="text-xs text-white/40 font-mono mt-1">{g.id}</p>
                  </div>
                  <div className="text-right text-sm">
                    <p className="text-white/70">
                      设备数{" "}
                      <span className="text-cyan-400/90">{g.agent_count}</span>
                    </p>
                    <p className="text-xs text-white/45 mt-1">
                      我的组内角色：{" "}
                      <span className="text-amber-400/90">
                        {g.my_group_role
                          ? formatDeviceGroupRole(g.my_group_role)
                          : "未单独授权（沿用组织角色）"}
                      </span>
                    </p>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>

        {/* 权限说明矩阵 */}
        <section className="rounded-2xl border border-white/10 bg-white/[0.02] p-6">
          <h2 className="text-sm font-semibold uppercase tracking-widest text-white/60 mb-4">
            组织角色说明（参考）
          </h2>
          <ul className="space-y-3 text-sm">
            {ORG_ROLES_ALL.map((role) => (
              <li
                key={role}
                className="border-l-2 border-cyan-500/30 pl-4 py-1 text-white/75"
              >
                <span className="text-cyan-400/90 font-medium">
                  {formatOrgRole(role)}
                </span>
                <span className="text-white/35 text-xs font-mono ml-2">({role})</span>
                <p className="text-white/55 text-xs mt-1 leading-relaxed">
                  {ORG_ROLE_DESCRIPTIONS[role as OrgRole]}
                </p>
              </li>
            ))}
          </ul>
        </section>
      </main>
    </ConsoleScaffold>
  );
}
