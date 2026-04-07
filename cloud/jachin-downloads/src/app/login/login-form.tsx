"use client";

import { useState } from "react";
import { signIn } from "next-auth/react";
import { useSearchParams } from "next/navigation";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

type Props = { showGithub: boolean };

/**
 * 与 Nexus 一致：邮箱密码需 `users.password_hash` 非空；纯 GitHub 账号须走 GitHub 登录。
 */
export function LoginForm({ showGithub }: Props) {
  const searchParams = useSearchParams();
  const callbackUrl = searchParams.get("callbackUrl") ?? "/";
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const res = await signIn("credentials", {
        email: email.trim().toLowerCase(),
        password,
        redirect: false,
        callbackUrl,
      });
      if (res?.error) {
        setError("邮箱或密码错误");
        return;
      }
      window.location.href = callbackUrl;
    } finally {
      setLoading(false);
    }
  }

  async function onGitHub() {
    setError(null);
    setLoading(true);
    try {
      await signIn("github", { callbackUrl });
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-screen flex-col items-center justify-center px-4">
      <div
        className={cn(
          "w-full max-w-md rounded-xl border border-white/10 bg-white/[0.03] p-8",
          "shadow-[0_0_60px_-12px_rgba(34,211,238,0.25)]"
        )}
      >
        <h1 className="mb-1 text-center text-xl font-semibold tracking-tight text-white">
          桌面端发行大厅
        </h1>
        <p className="mb-8 text-center text-sm text-white/45">使用 Nexus 账号登录</p>

        <form onSubmit={onSubmit} className="space-y-4">
          <div>
            <label className="mb-1.5 block text-xs font-medium text-white/60">邮箱</label>
            <input
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full rounded-md border border-white/10 bg-black/40 px-3 py-2 text-sm text-white outline-none ring-cyan-500/30 focus:ring-2"
            />
          </div>
          <div>
            <label className="mb-1.5 block text-xs font-medium text-white/60">密码</label>
            <input
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full rounded-md border border-white/10 bg-black/40 px-3 py-2 text-sm text-white outline-none ring-cyan-500/30 focus:ring-2"
            />
          </div>
          {error ? (
            <p className="text-center text-sm text-rose-400/90">{error}</p>
          ) : null}
          <Button type="submit" className="w-full" disabled={loading}>
            {loading ? "登录中…" : "登录"}
          </Button>
        </form>

        {showGithub ? (
          <>
            <div className="relative my-6">
              <div className="absolute inset-0 flex items-center">
                <span className="w-full border-t border-white/10" />
              </div>
              <div className="relative flex justify-center text-xs">
                <span className="bg-[#0a0a0c] px-2 text-white/35">或</span>
              </div>
            </div>
            <Button
              type="button"
              variant="outline"
              className="w-full border-white/15 bg-white/[0.04] text-white hover:bg-white/[0.08]"
              disabled={loading}
              onClick={() => void onGitHub()}
            >
              使用 GitHub 登录
            </Button>
            <p className="mt-3 text-center text-[11px] leading-relaxed text-white/35">
              若 Nexus 上只用 GitHub 注册、未设密码，请用 GitHub 登录；邮箱密码登录需在库中有
              password_hash。
            </p>
          </>
        ) : null}
      </div>
    </div>
  );
}
