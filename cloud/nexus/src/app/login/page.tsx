/**
 * Nexus 登录 / 注册页：Auth.js Credentials + `/api/auth/register`。
 * 注册走「零感知生根」单事务（users + 个人 organizations + organization_users.owner），
 * 登录后会话 JWT 含 orgId/orgRole；与 `middleware.ts` Default Deny 白名单 `/login` 对齐。
 */
"use client";

import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { signIn } from "next-auth/react";
import { Loader2 } from "lucide-react";

function LoginForm() {
  const searchParams = useSearchParams();
  const callbackUrl =
    searchParams.get("callbackUrl") ||
    searchParams.get("redirect") ||
    "/console";
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleLogin(e: React.FormEvent) {
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
        setLoading(false);
        return;
      }
      window.location.href = res?.url ?? callbackUrl;
    } catch {
      setError("登录失败，请重试");
      setLoading(false);
    }
  }

  async function handleRegister(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const r = await fetch("/api/auth/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: email.trim().toLowerCase(),
          password,
          name: name.trim() || undefined,
        }),
      });
      const data = (await r.json()) as {
        success?: boolean;
        message?: string;
      };
      if (!r.ok || !data.success) {
        setError(data.message ?? "注册失败");
        setLoading(false);
        return;
      }
      const res = await signIn("credentials", {
        email: email.trim().toLowerCase(),
        password,
        redirect: false,
        callbackUrl,
      });
      if (res?.error) {
        setError("注册成功但自动登录失败，请手动登录");
        setLoading(false);
        setMode("login");
        return;
      }
      window.location.href = res?.url ?? callbackUrl;
    } catch {
      setError("注册失败，请重试");
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen bg-[#030303] flex flex-col items-center justify-center p-4">
      <div
        className="fixed inset-0 -z-10 pointer-events-none"
        style={{
          background: `
            radial-gradient(ellipse 80% 50% at 50% 0%, rgba(34, 211, 238, 0.12) 0%, transparent 60%),
            radial-gradient(ellipse 60% 40% at 50% 80%, rgba(168, 85, 247, 0.06) 0%, transparent 50%),
            #030303
          `,
        }}
      />

      <div className="w-full max-w-md rounded-2xl backdrop-blur-xl border border-white/10 bg-white/[0.02] p-8 md:p-10">
        <h1 className="text-xl font-semibold text-white text-center mb-1">
          Jachin Nexus
        </h1>
        <p className="text-sm text-white/50 text-center mb-8">
          {mode === "login" ? "登录以继续" : "注册账号（自动创建个人工作区）"}
        </p>

        <div className="flex rounded-lg bg-white/5 p-1 mb-6">
          <button
            type="button"
            onClick={() => {
              setMode("login");
              setError(null);
            }}
            className={`flex-1 rounded-md py-2 text-sm font-medium transition ${
              mode === "login"
                ? "bg-white/10 text-white"
                : "text-white/50 hover:text-white/80"
            }`}
          >
            登录
          </button>
          <button
            type="button"
            onClick={() => {
              setMode("register");
              setError(null);
            }}
            className={`flex-1 rounded-md py-2 text-sm font-medium transition ${
              mode === "register"
                ? "bg-white/10 text-white"
                : "text-white/50 hover:text-white/80"
            }`}
          >
            注册
          </button>
        </div>

        <form
          onSubmit={mode === "login" ? handleLogin : handleRegister}
          className="space-y-4"
        >
          {mode === "register" && (
            <div>
              <label className="block text-xs text-white/50 mb-1.5">
                显示名（可选）
              </label>
              <input
                type="text"
                autoComplete="name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="w-full rounded-lg border border-white/10 bg-black/30 px-3 py-2.5 text-sm text-white placeholder:text-white/30 focus:border-cyan-500/50 focus:outline-none focus:ring-1 focus:ring-cyan-500/30"
                placeholder="昵称"
              />
            </div>
          )}
          <div>
            <label className="block text-xs text-white/50 mb-1.5">邮箱</label>
            <input
              type="email"
              required
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full rounded-lg border border-white/10 bg-black/30 px-3 py-2.5 text-sm text-white placeholder:text-white/30 focus:border-cyan-500/50 focus:outline-none focus:ring-1 focus:ring-cyan-500/30"
              placeholder="you@example.com"
            />
          </div>
          <div>
            <label className="block text-xs text-white/50 mb-1.5">密码</label>
            <input
              type="password"
              required
              autoComplete={
                mode === "login" ? "current-password" : "new-password"
              }
              minLength={mode === "register" ? 8 : undefined}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full rounded-lg border border-white/10 bg-black/30 px-3 py-2.5 text-sm text-white placeholder:text-white/30 focus:border-cyan-500/50 focus:outline-none focus:ring-1 focus:ring-cyan-500/30"
              placeholder={mode === "register" ? "至少 8 位" : "••••••••"}
            />
          </div>

          {error && (
            <p className="text-sm text-red-400/90 text-center">{error}</p>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full flex items-center justify-center gap-2 rounded-lg bg-cyan-600/90 hover:bg-cyan-500 text-white font-medium py-2.5 text-sm transition disabled:opacity-50"
          >
            {loading ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : mode === "login" ? (
              "登录"
            ) : (
              "注册并登录"
            )}
          </button>
        </form>

        <p className="mt-6 text-center text-xs text-white/40">
          <Link href="/" className="text-cyan-400/80 hover:text-cyan-300">
            返回首页
          </Link>
        </p>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen bg-[#030303] flex items-center justify-center">
          <Loader2 className="w-8 h-8 text-cyan-400 animate-spin" />
        </div>
      }
    >
      <LoginForm />
    </Suspense>
  );
}
