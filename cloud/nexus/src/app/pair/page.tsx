"use client";

import { useState } from "react";
import Link from "next/link";
import Navbar from "@/components/Navbar";

export default function PairPage() {
  const [code, setCode] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<{ success: boolean; message?: string } | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!code.trim()) return;
    setLoading(true);
    setResult(null);
    try {
      const res = await fetch("/api/v1/pairing/confirm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: code.trim().toUpperCase() }),
      });
      const data = await res.json();
      if (res.ok && data.success) {
        setResult({ success: true, message: `边缘智能体 ${data.instance_id} 已绑定` });
        setCode("");
      } else {
        setResult({ success: false, message: data.error || "授权失败" });
      }
    } catch (err) {
      setResult({ success: false, message: String(err) });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-[#050505]">
      <div
        className="fixed inset-0 -z-10 pointer-events-none"
        style={{
          background: `
            radial-gradient(ellipse 60% 40% at 50% 20%, rgba(168, 85, 247, 0.08) 0%, transparent 50%),
            #050505
          `,
        }}
      />

      <Navbar />

      <main className="pt-24 px-6 max-w-md mx-auto">
        <h1 className="text-2xl font-bold text-purple-400 mb-2">边缘智能体配对</h1>
        <p className="text-white/60 text-sm mb-8">
          在 Layer 2 终端执行 <code className="text-violet-400">jachin pair</code> 获取 6 位码，输入下方完成授权绑定。
        </p>

        <form onSubmit={handleSubmit} className="space-y-4">
          <input
            type="text"
            value={code}
            onChange={(e) => setCode(e.target.value.toUpperCase().slice(0, 6))}
            placeholder="输入 6 位配对码"
            maxLength={6}
            className="w-full px-4 py-3 rounded-lg bg-white/5 border border-violet-500/30 text-white placeholder-white/40 focus:outline-none focus:border-violet-500"
            autoFocus
          />
          <button
            type="submit"
            disabled={loading || code.length < 6}
            className="w-full py-3 rounded-lg font-medium bg-violet-500 hover:bg-violet-600 disabled:opacity-50 disabled:cursor-not-allowed text-white transition-colors"
          >
            {loading ? "授权中..." : "授权绑定"}
          </button>
        </form>

        {result && (
          <div
            className={`mt-6 p-4 rounded-lg ${
              result.success ? "bg-green-500/10 text-green-400" : "bg-red-500/10 text-red-400"
            }`}
          >
            {result.success ? result.message : result.message}
          </div>
        )}

        <p className="mt-8 text-white/40 text-sm">
          <Link href="/console" className="text-violet-400 hover:underline">
            返回指挥台
          </Link>
        </p>
      </main>
    </div>
  );
}
