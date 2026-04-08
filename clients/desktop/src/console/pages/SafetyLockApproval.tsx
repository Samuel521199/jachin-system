/**
 * 安全锁审批 — 列出 pending JSON，审批后写入 JACHIN_SAFETY_LOCK.md（L3 与 CLI 同源逻辑）
 * 管理员密钥仅保存在本机：用户输入 + 可选 localStorage，勿提交到仓库。
 */

import { useCallback, useEffect, useState } from "react";
import { Loader2, RefreshCw, ShieldCheck } from "lucide-react";
import {
  approveSafetyLockPending,
  fetchSafetyLockPending,
  rejectSafetyLockPending,
  type SafetyLockPendingItem,
} from "../../lib/api";
import { cn } from "../../utils/cn";

const LS_TOKEN_KEY = "jachin_console_safety_lock_admin_token";

function formatTags(tags: unknown): string {
  if (Array.isArray(tags)) return tags.map(String).join(", ");
  return "";
}

export function SafetyLockApproval() {
  const [token, setToken] = useState("");
  const [rememberToken, setRememberToken] = useState(false);
  const [items, setItems] = useState<SafetyLockPendingItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [banner, setBanner] = useState<{ kind: "ok" | "err" | "info"; text: string } | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  useEffect(() => {
    try {
      const saved = localStorage.getItem(LS_TOKEN_KEY);
      if (saved) {
        setToken(saved);
        setRememberToken(true);
      }
    } catch {
      /* ignore */
    }
  }, []);

  const persistToken = useCallback((t: string, remember: boolean) => {
    try {
      if (remember && t.trim()) localStorage.setItem(LS_TOKEN_KEY, t.trim());
      else localStorage.removeItem(LS_TOKEN_KEY);
    } catch {
      /* ignore */
    }
  }, []);

  const loadPending = useCallback(async () => {
    const t = token.trim();
    if (!t) {
      setBanner({ kind: "err", text: "请先填写管理员密钥（与 L3 环境变量 JACHIN_SAFETY_LOCK_ADMIN_TOKEN 一致）。" });
      return;
    }
    setLoading(true);
    setBanner(null);
    try {
      const res = await fetchSafetyLockPending(t);
      if (!res.ok) {
        setItems([]);
        setBanner({
          kind: "err",
          text: res.message || res.error || "加载失败（请确认 L3 已启动且已配置管理员 token）",
        });
        return;
      }
      setItems(res.items ?? []);
      setBanner({
        kind: "info",
        text: res.count === 0 ? "当前没有待审批条目。" : `共 ${res.count} 条待审批。`,
      });
    } catch (e) {
      setItems([]);
      setBanner({ kind: "err", text: (e as Error)?.message || "网络错误，请确认 L3 HTTP 可达。" });
    } finally {
      setLoading(false);
    }
  }, [token]);

  const onApprove = async (pendingId: string) => {
    const t = token.trim();
    if (!t) return;
    setBusyId(pendingId);
    setBanner(null);
    try {
      const r = await approveSafetyLockPending(t, pendingId);
      if (r.ok) {
        const msg = `已审批：${pendingId}${r.entry_id ? ` → 正式条目 id=\`${r.entry_id}\`` : ""}`;
        await loadPending();
        setBanner({ kind: "ok", text: msg });
      } else {
        setBanner({ kind: "err", text: r.message || r.error || "审批失败" });
      }
    } catch (e) {
      setBanner({ kind: "err", text: (e as Error)?.message || "审批请求失败" });
    } finally {
      setBusyId(null);
    }
  };

  const onReject = async (pendingId: string) => {
    const t = token.trim();
    if (!t) return;
    if (!window.confirm(`确定拒绝并删除 pending「${pendingId}」？此操作不可恢复。`)) return;
    setBusyId(pendingId);
    setBanner(null);
    try {
      const r = await rejectSafetyLockPending(t, pendingId);
      if (r.ok) {
        const msg = r.message || `已拒绝：${pendingId}`;
        await loadPending();
        setBanner({ kind: "ok", text: msg });
      } else {
        setBanner({ kind: "err", text: r.message || r.error || "拒绝失败" });
      }
    } catch (e) {
      setBanner({ kind: "err", text: (e as Error)?.message || "拒绝请求失败" });
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="h-full flex flex-col p-6 overflow-auto">
      <header className="flex-shrink-0 mb-6 flex flex-wrap items-start gap-4 justify-between">
        <div>
          <h1
            className="font-sci-fi text-2xl font-bold text-transparent bg-clip-text bg-gradient-to-r from-rose-400 to-cyan-400 flex items-center gap-2"
            style={{ fontFamily: "Orbitron, sans-serif" }}
          >
            <ShieldCheck className="w-8 h-8 text-cyan-400/90 flex-shrink-0" />
            安全锁审批
          </h1>
          <p className="text-slate-400 text-sm mt-2 max-w-2xl">
            待审批内容来自 Agent 在安全锁学习模式下的 pending；点击「审批」后写入本机{" "}
            <code className="text-cyan-300/90">JACHIN_SAFETY_LOCK.md</code>
            ，与 CLI <code className="text-slate-500">jachin_safety_lock_admin approve</code> 等价。
          </p>
        </div>
      </header>

      <section className="rounded-2xl border border-white/10 bg-slate-900/40 backdrop-blur-sm p-5 mb-6 space-y-4">
        <div className="grid gap-3 sm:grid-cols-[1fr_auto_auto] sm:items-end">
          <div>
            <label className="block text-xs uppercase tracking-wider text-slate-500 mb-1">
              管理员密钥
            </label>
            <input
              type="password"
              autoComplete="off"
              value={token}
              onChange={(e) => {
                setToken(e.target.value);
                if (rememberToken) persistToken(e.target.value, true);
              }}
              placeholder="与 L3 进程 JACHIN_SAFETY_LOCK_ADMIN_TOKEN 相同"
              className="w-full rounded-xl bg-slate-950/80 border border-white/10 px-4 py-2.5 text-sm text-slate-200 placeholder:text-slate-600 focus:outline-none focus:ring-2 focus:ring-cyan-500/40"
            />
          </div>
          <label className="flex items-center gap-2 text-sm text-slate-400 cursor-pointer sm:pb-2">
            <input
              type="checkbox"
              checked={rememberToken}
              onChange={(e) => {
                const v = e.target.checked;
                setRememberToken(v);
                persistToken(token, v);
              }}
              className="rounded border-white/20 bg-slate-900"
            />
            记住本机
          </label>
          <button
            type="button"
            onClick={() => {
              persistToken(token, rememberToken);
              void loadPending();
            }}
            disabled={loading}
            className="inline-flex items-center justify-center gap-2 rounded-xl bg-cyan-500/20 text-cyan-300 border border-cyan-400/40 px-5 py-2.5 text-sm font-medium hover:bg-cyan-500/30 disabled:opacity-50"
          >
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
            刷新列表
          </button>
        </div>
        {banner && (
          <div
            className={cn(
              "rounded-xl px-4 py-3 text-sm",
              banner.kind === "ok" && "bg-emerald-500/10 text-emerald-200 border border-emerald-500/30",
              banner.kind === "err" && "bg-rose-500/10 text-rose-200 border border-rose-500/30",
              banner.kind === "info" && "bg-slate-500/10 text-slate-300 border border-white/10"
            )}
          >
            {banner.text}
          </div>
        )}
      </section>

      <div className="space-y-4 flex-1 min-h-0">
        {items.length === 0 && !loading && (
          <p className="text-slate-500 text-sm text-center py-12">无条目，或尚未加载。点击「刷新列表」。</p>
        )}
        {items.map((it, idx) => {
          const id = it.pending_id || "";
          const busy = busyId === id;
          return (
            <article
              key={id || `row-${idx}`}
              className="rounded-2xl border border-white/10 bg-slate-900/30 p-5 flex flex-col gap-4"
            >
              <div className="flex flex-wrap items-center gap-3 text-xs text-slate-500">
                <span className="font-mono text-cyan-400/90">{id}</span>
                {it.created_at && <span>{it.created_at}</span>}
                {it.source && (
                  <span>
                    source: <code className="text-slate-400">{it.source}</code>
                  </span>
                )}
                {formatTags(it.tags) && (
                  <span>
                    tags: <span className="text-slate-400">{formatTags(it.tags)}</span>
                  </span>
                )}
              </div>
              <pre className="text-sm text-slate-300 whitespace-pre-wrap break-words max-h-64 overflow-y-auto rounded-xl bg-black/30 border border-white/5 p-4 font-sans">
                {it.body || "（空正文）"}
              </pre>
              <div className="flex flex-wrap gap-3">
                <button
                  type="button"
                  disabled={busy || !token.trim()}
                  onClick={() => void onApprove(id)}
                  className="inline-flex items-center gap-2 rounded-xl bg-rose-500/25 text-rose-200 border border-rose-400/50 px-6 py-2.5 text-sm font-semibold hover:bg-rose-500/35 disabled:opacity-40"
                >
                  {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
                  审批
                </button>
                <button
                  type="button"
                  disabled={busy || !token.trim()}
                  onClick={() => void onReject(id)}
                  className="inline-flex items-center gap-2 rounded-xl bg-slate-800 text-slate-300 border border-white/15 px-5 py-2.5 text-sm hover:bg-slate-700 disabled:opacity-40"
                >
                  拒绝
                </button>
              </div>
            </article>
          );
        })}
      </div>
    </div>
  );
}
