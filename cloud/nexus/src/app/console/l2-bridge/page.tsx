"use client";

import { Suspense, useCallback, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import Navbar from "@/components/Navbar";
import { Link2, Loader2 } from "lucide-react";

function L2BridgeInner() {
  const searchParams = useSearchParams();
  const rawReturn = searchParams.get("return_to")?.trim() || "";

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const authorize = useCallback(async () => {
    setError(null);
    if (!rawReturn) {
      setError("缺少参数 return_to。请从 Layer 2 网关的「Nexus 账号登录」入口进入本页。");
      return;
    }
    setLoading(true);
    try {
      const mintRes = await fetch("/api/v1/l2-bridge/mint", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ return_to: rawReturn }),
      });
      const mintData = await mintRes.json().catch(() => ({}));
      if (!mintRes.ok) {
        setError(
          mintData.message ||
            mintData.error ||
            `生成绑定码失败（HTTP ${mintRes.status}）`,
        );
        return;
      }
      const code = mintData.bridge_code as string | undefined;
      if (!code) {
        setError("响应缺少 bridge_code");
        return;
      }
      const sep = rawReturn.includes("?") ? "&" : "?";
      window.location.href = `${rawReturn}${sep}bridge_code=${encodeURIComponent(code)}`;
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [rawReturn]);

  return (
    <div className="min-h-screen bg-[#030303]">
      <div
        className="fixed inset-0 -z-10 pointer-events-none"
        style={{
          background: `
            radial-gradient(ellipse 80% 50% at 50% 0%, rgba(168, 85, 247, 0.1) 0%, transparent 60%),
            #030303
          `,
        }}
      />
      <Navbar />
      <main className="pt-28 px-6 flex flex-col items-center min-h-[calc(100vh-8rem)]">
        <div className="w-full max-w-lg rounded-2xl border border-white/10 bg-white/[0.02] p-8 md:p-10">
          <h1 className="text-xl md:text-2xl font-bold text-violet-400 mb-2 tracking-wide flex items-center gap-2">
            <Link2 className="w-7 h-7" />
            绑定 Layer 2 控制面
          </h1>
          <p className="text-white/50 text-sm mb-6">
            使用当前 Nexus 会话授权一台 L2（浏览器跳转路径）。若 L2 网关已支持 L1
            邮箱+密码直连登录，可不必经过本页。无头环境请用{" "}
            <Link href="/console/pair" className="text-cyan-400 hover:underline">
              6 位配对码
            </Link>
            。
          </p>

          {rawReturn ? (
            <p className="text-xs text-white/40 font-mono break-all mb-6 p-3 rounded-lg bg-black/30 border border-white/5">
              回跳：{rawReturn}
            </p>
          ) : (
            <p className="text-amber-400/90 text-sm mb-6">
              未检测到 return_to。请在 L2 网关点击「Nexus
              账号登录」，将自动打开本页并携带回跳地址。
            </p>
          )}

          {error && (
            <div className="mb-6 p-3 rounded-lg bg-red-500/10 border border-red-500/30 text-red-300 text-sm">
              {error}
            </div>
          )}

          <button
            type="button"
            disabled={loading || !rawReturn}
            onClick={authorize}
            className="w-full py-3 rounded-xl font-medium bg-violet-500/20 border border-violet-500/50 text-violet-200 hover:bg-violet-500/30 disabled:opacity-40 disabled:cursor-not-allowed flex items-center justify-center gap-2"
          >
            {loading ? (
              <>
                <Loader2 className="w-5 h-5 animate-spin" />
                正在授权…
              </>
            ) : (
              "确认授权并返回 L2"
            )}
          </button>

          <p className="mt-8 text-white/35 text-xs leading-relaxed">
            生产环境须在 L1 配置{" "}
            <code className="text-white/50">L2_BRIDGE_ALLOWED_RETURN_PREFIXES</code>{" "}
           （与 L2 公网基址一致，逗号分隔前缀）。
          </p>
        </div>
      </main>
    </div>
  );
}

export default function L2BridgePage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen bg-[#030303] flex items-center justify-center text-white/50">
          加载中…
        </div>
      }
    >
      <L2BridgeInner />
    </Suspense>
  );
}
