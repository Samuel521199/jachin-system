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
import { useNexusUiLang } from "@/components/NexusUiLangProvider";
import type { OrgRole } from "@/lib/org-constants";
import {
  ORG_ROLES_ALL,
  ORG_ROLES_CAN_INVITE,
  ORG_ROLES_INVITABLE,
} from "@/lib/org-constants";
import {
  formatDeviceGroupRoleI18n,
  formatOrgRoleI18n,
  nexusWorkspace,
  orgRoleDescriptionI18n,
} from "@/lib/nexus-ui-i18n";

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

export default function ConsoleWorkspacePage() {
  const { lang } = useNexusUiLang();
  const tw = nexusWorkspace[lang];
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
        setError(json.message ?? json.error ?? tw.errLoadOrgs);
        setOrgs([]);
        return;
      }
      const data = json.data;
      setActiveOrgId(data?.active_org_id ?? null);
      setOrgs(data?.organizations ?? []);
    } catch {
      setError(tw.errNetwork);
      setOrgs([]);
    } finally {
      setLoadingOrgs(false);
    }
  }, [tw.errLoadOrgs, tw.errNetwork]);

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
      setSuccessMsg(tw.successInvitePrefill);
      window.history.replaceState(
        null,
        "",
        `${window.location.pathname}`
      );
    }
  }, [status, tw.successInvitePrefill]);

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
        setError(json.message ?? tw.errCreate);
        return;
      }
      const oid = json.data?.org_id as string;
      setCreateName("");
      setSuccessMsg(tw.successCreate(String(json.data?.name ?? name)));
      await loadOrgs();
      if (oid) {
        await handleSwitchOrg(oid);
      }
    } catch {
      setError(tw.errCreate);
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
        setError(json.message ?? tw.errJoin);
        return;
      }
      const oid = json.data?.org_id as string;
      setJoinedOrgId(oid ?? null);
      setSuccessMsg(
        json.data?.already_member ? tw.joinAlreadyMember : tw.joinSuccess
      );
      setJoinToken("");
      await loadOrgs();
    } catch {
      setError(tw.errJoinGeneric);
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
        setError(json.message ?? tw.errInviteGen);
        return;
      }
      setInviteTokenOut(json.data?.token ?? null);
      setSuccessMsg(tw.successInviteGen);
    } catch {
      setError(tw.errInviteFailed);
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
      setError(tw.errCopy);
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
        setError(json.message ?? tw.errSwitch);
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
      setError(tw.errSwitch);
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
          <p className="text-white/60 mb-4">{tw.loginPrompt}</p>
          <Link
            href="/login?callbackUrl=/console/workspace"
            className="text-cyan-400 hover:text-cyan-300"
          >
            {tw.goLogin}
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
              {tw.title}
            </h1>
            <p className="text-sm text-white/50 mt-1">{tw.intro}</p>
          </div>
          <Link
            href="/console"
            className="text-sm text-white/50 hover:text-cyan-400 transition-colors"
          >
            {tw.backConsole}
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
            <p className="font-medium text-cyan-300/95 mb-1">{tw.onboardingTitle}</p>
            <p className="text-white/55 text-xs leading-relaxed">
              {tw.onboardingBody}
            </p>
          </div>
        ) : null}

        {/* 创建 / 加入 / 邀请 */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
          <section className="rounded-2xl border border-white/10 bg-white/[0.02] p-6">
            <h2 className="text-sm font-semibold uppercase tracking-widest text-cyan-400/90 flex items-center gap-2 mb-3">
              <PlusCircle className="w-4 h-4" />
              {tw.createTitle}
            </h2>
            <p className="text-xs text-white/45 mb-4">
              {tw.createDesc}
            </p>
            <div className="flex flex-col sm:flex-row gap-2">
              <input
                type="text"
                value={createName}
                onChange={(e) => setCreateName(e.target.value)}
                placeholder={tw.phWorkspaceName}
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
                  tw.createSubmit
                )}
              </button>
            </div>
          </section>

          <section className="rounded-2xl border border-white/10 bg-white/[0.02] p-6">
            <h2 className="text-sm font-semibold uppercase tracking-widest text-violet-400/90 flex items-center gap-2 mb-3">
              <UserPlus className="w-4 h-4" />
              {tw.joinTitle}
            </h2>
            <p className="text-xs text-white/45 mb-4">
              {tw.joinDesc}
            </p>
            <textarea
              value={joinToken}
              onChange={(e) => setJoinToken(e.target.value)}
              placeholder={tw.phJoinToken}
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
                  tw.joinBtn
                )}
              </button>
              {joinedOrgId ? (
                <button
                  type="button"
                  disabled={!!switching}
                  onClick={() => void handleSwitchOrg(joinedOrgId)}
                  className="px-4 py-2 rounded-lg bg-white/10 border border-white/20 text-white/90 text-sm hover:bg-white/15 disabled:opacity-40"
                >
                  {tw.switchJoined}
                </button>
              ) : null}
            </div>
          </section>
        </div>

        {canInvite ? (
          <section className="rounded-2xl border border-cyan-500/20 bg-cyan-950/10 p-6 mb-6">
            <h2 className="text-sm font-semibold uppercase tracking-widest text-cyan-400/90 flex items-center gap-2 mb-3">
              <Link2 className="w-4 h-4" />
              {tw.inviteTitle}
            </h2>
            <p className="text-xs text-white/45 mb-4">
              {tw.inviteIntroBefore}{" "}
              <span className="text-cyan-400/80 font-medium">
                {currentOrg?.name || u.orgId || tw.unknownOrg}
              </span>{" "}
              {tw.inviteIntroAfter}
            </p>
            <div className="flex flex-wrap items-end gap-3 mb-4">
              <div>
                <label className="block text-xs text-white/40 mb-1">{tw.labelInviteRole}</label>
                <select
                  value={inviteRole}
                  onChange={(e) => setInviteRole(e.target.value)}
                  className="rounded-lg bg-black/40 border border-white/15 px-3 py-2 text-sm text-white focus:border-cyan-500/50 focus:outline-none"
                >
                  {ORG_ROLES_INVITABLE.map((r) => (
                    <option key={r} value={r}>
                      {r}（{formatOrgRoleI18n(lang, r)}）
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-xs text-white/40 mb-1">{tw.labelTtl}</label>
                <select
                  value={inviteTtlSec}
                  onChange={(e) => setInviteTtlSec(Number(e.target.value))}
                  className="rounded-lg bg-black/40 border border-white/15 px-3 py-2 text-sm text-white focus:border-cyan-500/50 focus:outline-none"
                >
                  {tw.inviteTtl.map((p) => (
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
                  tw.generateInvite
                )}
              </button>
            </div>
            {inviteTokenOut ? (
              <div className="space-y-3 rounded-xl border border-white/10 bg-black/30 p-4">
                <p className="text-xs text-white/50">{tw.tokenHelp}</p>
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
                    {tw.copyToken}
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
                    {tw.copyLink}
                  </button>
                </div>
                <p className="text-[11px] text-white/35">
                  {tw.linkHashHint}
                </p>
              </div>
            ) : null}
          </section>
        ) : (
          <section className="rounded-2xl border border-white/10 bg-white/[0.02] p-4 mb-6 text-xs text-white/45">
            {tw.noInvite.replace(
              /\{role\}/g,
              formatOrgRoleI18n(lang, sessionOrgRole)
            )}
          </section>
        )}

        {/* 账号 */}
        <section className="rounded-2xl border border-white/10 bg-white/[0.02] p-6 mb-6">
          <h2 className="text-sm font-semibold uppercase tracking-widest text-violet-400/90 flex items-center gap-2 mb-4">
            <Shield className="w-4 h-4" />
            {tw.accountTitle}
          </h2>
          <dl className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-sm">
            <div>
              <dt className="text-white/40 mb-1">{tw.userId}</dt>
              <dd className="font-mono text-white/90 break-all">{u.id}</dd>
            </div>
            <div>
              <dt className="text-white/40 mb-1">{tw.emailName}</dt>
              <dd className="text-white/90">
                {u.email ?? "—"}
                {u.name ? (
                  <span className="text-white/50"> · {u.name}</span>
                ) : null}
              </dd>
            </div>
            <div>
              <dt className="text-white/40 mb-1">{tw.sessionOrgId}</dt>
              <dd className="font-mono text-cyan-400/90 text-xs break-all">
                {u.orgId || "—"}
              </dd>
            </div>
            <div>
              <dt className="text-white/40 mb-1">{tw.orgRoleHere}</dt>
              <dd className="text-white/90">
                <span className="text-cyan-400/90 font-medium">
                  {formatOrgRoleI18n(lang, sessionOrgRole)}
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
            {tw.orgListTitle}
          </h2>
          {loadingOrgs ? (
            <div className="flex items-center gap-2 text-white/50 text-sm">
              <Loader2 className="w-4 h-4 animate-spin" />
              {tw.loading}
            </div>
          ) : orgs.length === 0 ? (
            <p className="text-white/50 text-sm">{tw.orgEmpty}</p>
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
                        {tw.myRoleLine}{" "}
                        <span className="text-cyan-400/80">
                          {formatOrgRoleI18n(lang, o.role)}
                        </span>
                        {o.is_personal_default ? (
                          <span className="ml-2 text-violet-400/70">{tw.personalDefault}</span>
                        ) : null}
                      </p>
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      {isActive ? (
                        <span className="text-xs px-2 py-1 rounded-md bg-cyan-500/20 text-cyan-300 border border-cyan-500/30">
                          {tw.current}
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
                            tw.switchOrg
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
              {tw.footerOrgScope(currentOrg.name)}
            </p>
          ) : null}
        </section>

        {/* 成员 */}
        <section className="rounded-2xl border border-white/10 bg-white/[0.02] p-6 mb-6">
          <h2 className="text-sm font-semibold uppercase tracking-widest text-emerald-400/90 flex items-center gap-2 mb-4">
            <Users className="w-4 h-4" />
            {tw.membersTitle}
          </h2>
          {loadingMembers ? (
            <div className="flex items-center gap-2 text-white/50 text-sm">
              <Loader2 className="w-4 h-4 animate-spin" />
              {tw.loading}
            </div>
          ) : members.length === 0 ? (
            <p className="text-white/50 text-sm">{tw.membersEmpty}</p>
          ) : (
            <div className="overflow-x-auto rounded-xl border border-white/10">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-white/10 text-left text-xs text-white/45 uppercase tracking-wider">
                    <th className="py-3 px-3">{tw.thMember}</th>
                    <th className="py-3 px-3">{tw.thOrgRole}</th>
                    <th className="py-3 px-3">{tw.thJoined}</th>
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
                        <span className="text-cyan-400/85">{formatOrgRoleI18n(lang, m.role)}</span>
                        <span className="text-white/35 text-xs ml-1 font-mono">
                          ({m.role})
                        </span>
                      </td>
                      <td className="py-3 px-3 text-white/50 text-xs">
                        {m.joined_at
                          ? new Date(m.joined_at).toLocaleString(
                              lang === "en" ? "en-US" : "zh-CN"
                            )
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
            {tw.groupsTitle}
          </h2>
          <p className="text-xs text-white/45 mb-4">
            {tw.groupsIntro}
          </p>
          {loadingGroups ? (
            <div className="flex items-center gap-2 text-white/50 text-sm">
              <Loader2 className="w-4 h-4 animate-spin" />
              {tw.loading}
            </div>
          ) : groups.length === 0 ? (
            <p className="text-white/50 text-sm">
              {tw.groupsEmptyPrefix}
              <code className="text-white/60">device_groups</code>
              {tw.groupsEmptySuffix}
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
                      {tw.deviceCount}{" "}
                      <span className="text-cyan-400/90">{g.agent_count}</span>
                    </p>
                    <p className="text-xs text-white/45 mt-1">
                      {tw.myGroupRole}{" "}
                      <span className="text-amber-400/90">
                        {g.my_group_role
                          ? formatDeviceGroupRoleI18n(lang, g.my_group_role)
                          : tw.groupRoleFallback}
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
            {tw.rolesMatrixTitle}
          </h2>
          <ul className="space-y-3 text-sm">
            {ORG_ROLES_ALL.map((role) => (
              <li
                key={role}
                className="border-l-2 border-cyan-500/30 pl-4 py-1 text-white/75"
              >
                <span className="text-cyan-400/90 font-medium">
                  {formatOrgRoleI18n(lang, role)}
                </span>
                <span className="text-white/35 text-xs font-mono ml-2">({role})</span>
                <p className="text-white/55 text-xs mt-1 leading-relaxed">
                  {orgRoleDescriptionI18n(lang, role as OrgRole)}
                </p>
              </li>
            ))}
          </ul>
        </section>
      </main>
    </ConsoleScaffold>
  );
}
