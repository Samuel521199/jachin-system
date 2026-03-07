"use client";

import { Suspense, useEffect } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { Loader2 } from "lucide-react";

/**
 * 登录页直接跳转到控制台。
 * 后续可接入 Auth.js 实现登录。
 */
function LoginForm() {
  const searchParams = useSearchParams();
  const redirectTo = searchParams.get("redirect") || "/console";

  useEffect(() => {
    window.location.href = redirectTo;
  }, [redirectTo]);

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

      <div className="w-full max-w-md rounded-2xl backdrop-blur-xl border border-white/10 bg-white/[0.02] p-8 md:p-10 text-center">
        <Loader2 className="w-12 h-12 text-cyan-400 animate-spin mx-auto mb-4" />
        <p className="text-white/70">正在跳转至控制台...</p>
        <Link href={redirectTo} className="text-cyan-400 hover:underline mt-4 inline-block">
          若未自动跳转，请点击此处
        </Link>
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
