"use client";

import { useState, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { createClient } from "@/lib/supabase-auth/client";
import { Mail, Github, Loader2 } from "lucide-react";

function LoginForm() {
  const searchParams = useSearchParams();
  const redirectTo = searchParams.get("redirect") || "/console";
  const [email, setEmail] = useState("");
  const [loading, setLoading] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleMagicLink = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    const supabase = createClient();
    if (!supabase) {
      setError("Supabase 未配置，请设置 NEXT_PUBLIC_SUPABASE_URL 和 ANON_KEY");
      setLoading(false);
      return;
    }
    const { error: err } = await supabase.auth.signInWithOtp({
      email: email.trim(),
      options: {
        emailRedirectTo: `${window.location.origin}/auth/callback?next=${encodeURIComponent(redirectTo)}`,
      },
    });
    setLoading(false);
    if (err) {
      setError(err.message);
      return;
    }
    setSent(true);
  };

  const handleOAuth = async (provider: "github" | "google") => {
    setError(null);
    setLoading(true);
    const supabase = createClient();
    if (!supabase) {
      setError("Supabase 未配置");
      setLoading(false);
      return;
    }
    const { data, error: err } = await supabase.auth.signInWithOAuth({
      provider,
      options: {
        redirectTo: `${window.location.origin}/auth/callback?next=${encodeURIComponent(redirectTo)}`,
      },
    });
    setLoading(false);
    if (err) {
      setError(err.message);
      return;
    }
    if (data?.url) {
      window.location.href = data.url;
    }
  };

  return (
    <div className="min-h-screen bg-[#030303] flex flex-col items-center justify-center">
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
        <h1 className="text-2xl font-bold text-cyan-400/95 mb-1 tracking-wide">
          Jachin Nexus 控制中枢
        </h1>
        <p className="text-white/50 text-sm mb-8">
          请输入邮箱，首次使用将自动创建账号
        </p>

        {sent ? (
          <div className="text-cyan-400 text-sm py-4">
            验证链接已发送至 {email}，请查收邮箱并点击链接完成登录。
          </div>
        ) : (
          <form onSubmit={handleMagicLink} className="space-y-6">
            <div>
              <label className="block text-white/60 text-sm mb-2">邮箱</label>
              <div className="relative">
                <Mail className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-white/40" />
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="your@email.com"
                  required
                  disabled={loading}
                  className="w-full pl-12 pr-4 py-3 rounded-xl bg-black/40 border border-white/20 text-white placeholder-white/30 focus:outline-none focus:ring-2 focus:ring-cyan-500/50 focus:border-cyan-500/50 transition-all disabled:opacity-50"
                />
              </div>
            </div>

            <button
              type="submit"
              disabled={loading}
              className="w-full py-4 rounded-xl font-semibold flex items-center justify-center gap-2 bg-cyan-500/20 border border-cyan-500/50 text-cyan-400 hover:bg-cyan-500/30 hover:border-cyan-400/60 disabled:opacity-50 transition-all focus:outline-none focus:ring-2 focus:ring-cyan-500/50"
            >
              {loading ? (
                <Loader2 className="w-5 h-5 animate-spin" />
              ) : (
                "发送验证链接"
              )}
            </button>

            <div className="relative">
              <div className="absolute inset-0 flex items-center">
                <div className="w-full border-t border-white/10" />
              </div>
              <div className="relative flex justify-center text-sm">
                <span className="px-4 bg-transparent text-white/40">或</span>
              </div>
            </div>

            <div className="flex gap-4">
              <button
                type="button"
                onClick={() => handleOAuth("github")}
                disabled={loading}
                className="flex-1 py-3 rounded-xl font-medium flex items-center justify-center gap-2 bg-white/5 border border-white/10 text-white/90 hover:bg-white/10 hover:border-cyan-500/30 hover:shadow-[0_0_20px_rgba(34,211,238,0.15)] transition-all disabled:opacity-50"
              >
                <Github className="w-5 h-5" />
                GitHub
              </button>
              <button
                type="button"
                onClick={() => handleOAuth("google")}
                disabled={loading}
                className="flex-1 py-3 rounded-xl font-medium flex items-center justify-center gap-2 bg-white/5 border border-white/10 text-white/90 hover:bg-white/10 hover:border-cyan-500/30 hover:shadow-[0_0_20px_rgba(34,211,238,0.15)] transition-all disabled:opacity-50"
              >
                <svg className="w-5 h-5" viewBox="0 0 24 24">
                  <path fill="currentColor" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" />
                  <path fill="currentColor" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" />
                  <path fill="currentColor" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" />
                  <path fill="currentColor" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" />
                </svg>
                Google
              </button>
            </div>
          </form>
        )}

        {error && (
          <p className="mt-4 text-red-400 text-sm">{error}</p>
        )}
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={
      <div className="min-h-screen bg-[#030303] flex items-center justify-center">
        <Loader2 className="w-8 h-8 text-cyan-400 animate-spin" />
      </div>
    }>
      <LoginForm />
    </Suspense>
  );
}
