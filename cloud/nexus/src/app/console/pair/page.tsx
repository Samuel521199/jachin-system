"use client";

import { useState, useRef, useCallback, useEffect } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import Navbar from "@/components/Navbar";
import { Zap, CheckCircle2 } from "lucide-react";

const CODE_LEN = 6;

function parseCodeFromUrl(searchParams: URLSearchParams | null): string | null {
  if (!searchParams) return null;
  const code = searchParams.get("code")?.trim().toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, CODE_LEN);
  return code && code.length === CODE_LEN ? code : null;
}

export default function ConsolePairPage() {
  const searchParams = useSearchParams();
  const [digits, setDigits] = useState<string[]>(Array(CODE_LEN).fill(""));
  const [loading, setLoading] = useState(false);
  const [success, setSuccess] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRefs = useRef<(HTMLInputElement | null)[]>([]);

  const code = digits.join("").toUpperCase();

  // Battle C: prefill from ?code=XXX (scan-to-connect)
  useEffect(() => {
    const urlCode = parseCodeFromUrl(searchParams);
    if (urlCode) {
      setDigits(urlCode.split(""));
    }
  }, [searchParams]);

  const handleChange = useCallback(
    (i: number, v: string) => {
      const char = v.slice(-1).toUpperCase();
      if (char && !/^[A-Z0-9]$/.test(char)) return;
      const next = [...digits];
      next[i] = char;
      setDigits(next);
      setError(null);
      if (char && i < CODE_LEN - 1) {
        inputRefs.current[i + 1]?.focus();
      }
    },
    [digits]
  );

  const handleKeyDown = useCallback(
    (i: number, e: React.KeyboardEvent) => {
      if (e.key === "Backspace" && !digits[i] && i > 0) {
        inputRefs.current[i - 1]?.focus();
        const next = [...digits];
        next[i - 1] = "";
        setDigits(next);
      }
    },
    [digits]
  );

  const handlePaste = useCallback(
    (e: React.ClipboardEvent) => {
      e.preventDefault();
      const pasted = e.clipboardData
        .getData("text")
        .toUpperCase()
        .replace(/[^A-Z0-9]/g, "")
        .slice(0, CODE_LEN)
        .split("");
      if (pasted.length === 0) return;
      const next = [...digits];
      pasted.forEach((c, i) => {
        if (i < CODE_LEN) next[i] = c;
      });
      setDigits(next);
      setError(null);
      const lastFilled = Math.min(pasted.length, CODE_LEN) - 1;
      inputRefs.current[lastFilled]?.focus();
    },
    [digits]
  );

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const raw = code.replace(/-/g, "");
    if (raw.length !== CODE_LEN) return;
    setLoading(true);
    setError(null);
    setSuccess(false);
    try {
      const res = await fetch("/api/v1/pairing/confirm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code: raw }),
      });
      const data = await res.json();
      if (res.ok && data.success) {
        setSuccess(true);
        setDigits(Array(CODE_LEN).fill(""));
      } else {
        setError(data.error || "授权失败");
      }
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-[#030303]">
      <div
        className="fixed inset-0 -z-10 pointer-events-none"
        style={{
          background: `
            radial-gradient(ellipse 80% 50% at 50% 0%, rgba(34, 211, 238, 0.12) 0%, transparent 60%),
            radial-gradient(ellipse 60% 40% at 50% 80%, rgba(34, 211, 238, 0.04) 0%, transparent 50%),
            #030303
          `,
        }}
      />

      <Navbar />

      <main className="pt-28 px-6 flex flex-col items-center justify-center min-h-[calc(100vh-8rem)]">
        <div
          className={`w-full max-w-md rounded-2xl backdrop-blur-xl border transition-all duration-500 ${
            success
              ? "border-green-500/60 shadow-[0_0_30px_rgba(34,197,94,0.2)]"
              : "border-white/10 bg-white/[0.02]"
          }`}
        >
          <div className="p-8 md:p-10">
            <h1 className="text-xl md:text-2xl font-bold text-cyan-400/95 mb-1 tracking-wide">
              激活边缘智能体
            </h1>
            <p className="text-sm text-white/50 mb-8">
              Activate Edge Agent
            </p>
            <p className="text-white/60 text-sm mb-6">
              请输入 CLI 终端上显示的 6 位神经配对码。
            </p>

            {success ? (
              <div className="flex flex-col items-center py-6">
                <CheckCircle2 className="w-16 h-16 text-green-400 mb-4" strokeWidth={1.5} />
                <p className="text-green-400 font-medium text-center">
                  神经元已成功接入星图！终端设备即将响应。
                </p>
                <p className="text-white/50 text-sm mt-2">
                  Neural link established. Terminal will respond shortly.
                </p>
                <button
                  type="button"
                  onClick={() => setSuccess(false)}
                  className="mt-6 text-cyan-400 hover:text-cyan-300 text-sm"
                >
                  继续添加
                </button>
              </div>
            ) : (
              <form onSubmit={handleSubmit} className="space-y-6">
                <div
                  className="flex gap-2 justify-center"
                  onPaste={handlePaste}
                >
                  {digits.map((d, i) => (
                    <input
                      key={i}
                      ref={(el) => {
                        inputRefs.current[i] = el;
                      }}
                      type="text"
                      inputMode="text"
                      maxLength={1}
                      value={d}
                      onChange={(e) => handleChange(i, e.target.value)}
                      onKeyDown={(e) => handleKeyDown(i, e)}
                      className="w-12 h-14 md:w-14 md:h-16 text-center text-xl md:text-2xl font-mono font-bold bg-black/40 border border-white/20 rounded-lg text-cyan-400 placeholder-white/20 focus:outline-none focus:ring-2 focus:ring-cyan-500 focus:ring-offset-0 focus:ring-offset-black focus:border-cyan-500/50 shadow-[0_0_20px_rgba(34,211,238,0.15)] transition-all"
                      placeholder="·"
                      disabled={loading}
                    />
                  ))}
                </div>

                <button
                  type="submit"
                  disabled={loading || code.length !== CODE_LEN}
                  className="w-full py-4 rounded-xl font-semibold flex items-center justify-center gap-2 bg-cyan-500/20 border border-cyan-500/50 text-cyan-400 hover:bg-cyan-500/30 hover:border-cyan-400/60 disabled:opacity-50 disabled:cursor-not-allowed transition-all focus:outline-none focus:ring-2 focus:ring-cyan-500/50"
                >
                  {loading ? (
                    <>
                      <span className="animate-pulse">正在注入灵魂...</span>
                      <span className="text-white/50 text-sm">/ Injecting Soul...</span>
                    </>
                  ) : (
                    <>
                      <Zap className="w-5 h-5" />
                      建立神经连接 (Establish Neural Link)
                    </>
                  )}
                </button>

                {error && (
                  <p className="text-red-400 text-sm text-center">{error}</p>
                )}
              </form>
            )}
          </div>
        </div>

        <p className="mt-8 text-white/40 text-sm">
          <Link href="/console" className="text-cyan-400/80 hover:text-cyan-400 hover:underline">
            返回指挥台
          </Link>
        </p>
      </main>
    </div>
  );
}
