/**
 * Nexus 登录 / 注册页：Auth.js Credentials + `/api/auth/register`。
 * 注册仅创建 users；工作区须在 `/console/workspace` 创建或加入。
 * 登录后会话 JWT 含 orgId/orgRole（无工作区时为空，控制台会引导至工作区页）。
 */
"use client";

import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { signIn } from "next-auth/react";
import { Loader2 } from "lucide-react";

function isLoopbackHost(hostname: string): boolean {
  const h = hostname.toLowerCase();
  return (
    h === "localhost" ||
    h === "127.0.0.1" ||
    h === "::1" ||
    h === "[::1]"
  );
}

/**
 * 1) 中间件可能带 callbackUrl=http://0.0.0.0:3000/...（监听地址），浏览器打不开。
 * 2) AUTH_URL / Auth.js 默认常为 http://localhost:3000；用户用局域网 IP 访问时，
 *    signIn 返回的 res.url 仍是 localhost，会跳到本机而非服务器——用当前页 origin 替换。
 */
function getSafeCallbackUrl(raw: string): string {
  if (typeof window === "undefined") return raw;
  const t = raw.trim();
  if (!t.startsWith("http://") && !t.startsWith("https://")) {
    return t;
  }
  try {
    const u = new URL(t);
    const browserHost = window.location.hostname;
    if (u.hostname === "0.0.0.0" || u.hostname === "[::]") {
      u.protocol = window.location.protocol;
      u.host = window.location.host;
      return u.toString();
    }
    if (isLoopbackHost(u.hostname) && !isLoopbackHost(browserHost)) {
      u.protocol = window.location.protocol;
      u.host = window.location.host;
      return u.toString();
    }
  } catch {
    return raw;
  }
  return raw;
}

/** 登录成功后的跳转：优先用服务端返回 URL，但须校正 loopback / 0.0.0.0；相对路径原样使用。 */
/** NextAuth 任意失败常统一成 CredentialsSignin；JWT/库异常已在 auth.ts 兜底，此处区分其它 code 避免误导读用户改密码 */
function signInFailureMessage(error: string | undefined): string {
  if (!error || error === "CredentialsSignin") {
    return "邮箱或密码错误";
  }
  if (error === "Configuration") {
    return "登录服务配置异常（例如生产环境未设置 AUTH_SECRET）。请检查 Nexus 环境变量。";
  }
  return `登录失败（${error}）。若刚配置 DATABASE_URL，请重启 npm run dev 并确认 PostgreSQL 已启动。`;
}

function postLoginHref(
  serverUrl: string | null | undefined,
  fallback: string
): string {
  if (typeof window === "undefined") return fallback;
  const raw = (serverUrl && serverUrl.length > 0 ? serverUrl : fallback).trim();
  if (!raw.startsWith("http://") && !raw.startsWith("https://")) {
    return raw;
  }
  return getSafeCallbackUrl(raw);
}

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
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleLogin(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setNotice(null);
    setLoading(true);
    const safeCb = getSafeCallbackUrl(callbackUrl);
    try {
      // 密码原样交给 NextAuth，与 /api/auth/register 一致（见 passwordPlainForCredentials：不 trim）
      const res = await signIn("credentials", {
        email: email.trim().toLowerCase(),
        password,
        redirect: false,
        callbackUrl: safeCb,
      });
      if (res?.error) {
        setError(signInFailureMessage(res.error));
        setLoading(false);
        return;
      }
      window.location.href = postLoginHref(res?.url, safeCb);
    } catch {
      setError("登录失败，请重试");
      setLoading(false);
    }
  }

  async function handleRegister(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setNotice(null);
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
        password_recovered?: boolean;
      };
      if (!r.ok || !data.success) {
        setError(data.message ?? "注册失败");
        setLoading(false);
        return;
      }
      if (data.message && data.password_recovered) {
        setNotice(data.message);
      }
      // 自动登录使用与上一步注册请求相同的 password 状态（不 trim）
      const res = await signIn("credentials", {
        email: email.trim().toLowerCase(),
        password,
        redirect: false,
        callbackUrl: "/console/workspace",
      });
      if (res?.error) {
        setError(
          `注册成功但自动登录失败：${signInFailureMessage(res.error)} 请尝试手动登录。`
        );
        setLoading(false);
        setMode("login");
        return;
      }
      window.location.href = postLoginHref(res?.url, "/console/workspace");
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
          {mode === "login"
            ? "登录以继续"
            : "注册账号（登录后请创建或加入工作区）"}
        </p>

        <div className="flex rounded-lg bg-white/5 p-1 mb-6">
          <button
            type="button"
            onClick={() => {
              setMode("login");
              setError(null);
              setNotice(null);
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
              setNotice(null);
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

          {notice && (
            <p className="text-sm text-emerald-400/90 text-center">{notice}</p>
          )}
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
