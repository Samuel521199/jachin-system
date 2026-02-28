"use client";

import { useEffect, useState } from "react";
import { ShieldCheck } from "lucide-react";
import Navbar from "@/components/Navbar";

const AUDIT_LOGS = [
  "[10:45:01] Layer 2 blocked external access to Memory VectorDB.",
  "[10:42:12] Skill \"Weather\" downloaded. No local data exported.",
  "[10:38:33] Voice input processed locally. Zero cloud transmission.",
  "[10:35:22] Layer 2 blocked external access to Memory VectorDB.",
  "[10:30:15] Skill \"Calendar\" invoked. All data stays on device.",
  "[10:28:44] Intent analysis completed locally. No API call.",
  "[10:25:01] Layer 2 blocked external access to Memory VectorDB.",
  "[10:20:09] Persona \"傲娇女声\" loaded. Model cached locally.",
];

export default function ConsolePage() {
  const [logIndex, setLogIndex] = useState(0);

  useEffect(() => {
    const id = setInterval(() => {
      setLogIndex((i) => (i + 1) % AUDIT_LOGS.length);
    }, 2500);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="min-h-screen bg-zinc-950">
      {/* Subtle background gradient */}
      <div
        className="fixed inset-0 -z-10 pointer-events-none"
        style={{
          background: `
            radial-gradient(ellipse 60% 40% at 50% 20%, rgba(34, 197, 94, 0.06) 0%, transparent 50%),
            #09090b
          `,
        }}
      />

      <Navbar />

      <main className="pt-20 px-6 pb-16 max-w-5xl mx-auto">
        {/* Fleet Topology */}
        <section className="mb-16">
          <h2 className="text-xs font-semibold uppercase tracking-widest text-zinc-500 mb-6">
            Fleet Topology
          </h2>

          <div className="relative flex flex-col items-center">
            {/* Layer 3 Terminals - above center */}
            <div className="flex gap-6 justify-center mb-4">
              <div className="rounded-xl bg-zinc-900/80 border border-zinc-800 hover:border-zinc-700 p-4 min-w-[180px] transition-colors shadow-lg shadow-black/20">
                <div className="flex items-center gap-2 mb-2">
                  <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
                  <span className="text-xs uppercase tracking-wider text-emerald-400/90">Online</span>
                </div>
                <p className="text-xs text-zinc-400 mb-1">Layer 3 Terminal</p>
                <p className="text-white font-medium text-sm">书房全息终端</p>
              </div>
              <div className="rounded-xl bg-zinc-900/80 border border-zinc-800 hover:border-zinc-700 p-4 min-w-[180px] transition-colors shadow-lg shadow-black/20">
                <div className="flex items-center gap-2 mb-2">
                  <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
                  <span className="text-xs uppercase tracking-wider text-emerald-400/90">Online</span>
                </div>
                <p className="text-xs text-zinc-400 mb-1">Layer 3 Terminal</p>
                <p className="text-white font-medium text-sm">客厅 ESP32</p>
              </div>
            </div>

            {/* Connector lines to center */}
            <div className="flex gap-16 justify-center mb-2">
              <div className="w-px h-6 bg-zinc-700/60" />
              <div className="w-px h-6 bg-zinc-700/60" />
            </div>

            {/* Layer 2 Matrix - Center Big Card */}
            <div className="rounded-2xl bg-zinc-900/90 border-2 border-zinc-800 hover:border-zinc-600 p-8 min-w-[320px] max-w-md transition-all shadow-xl shadow-black/30 ring-1 ring-emerald-500/10">
              <div className="flex items-center gap-3 mb-4">
                <span className="w-3 h-3 rounded-full bg-emerald-500 animate-pulse shadow-lg shadow-emerald-500/50" />
                <span className="text-sm font-semibold uppercase tracking-widest text-emerald-400">
                  完美运行
                </span>
                <span className="text-zinc-500">(Online)</span>
              </div>
              <p className="text-zinc-400 text-sm mb-1">Layer 2 Matrix</p>
              <p className="text-xl font-semibold text-white">
                家庭私有服务器
              </p>
              <p className="text-xs text-zinc-500 mt-3">
                Primary Node · Ray Cluster Active
              </p>
            </div>
          </div>
        </section>

        {/* Privacy Audit Dashboard */}
        <section className="mt-20">
          <h2 className="text-xs font-semibold uppercase tracking-widest text-zinc-500 mb-6">
            Privacy Audit
          </h2>

          <div className="rounded-2xl bg-zinc-900/80 border border-zinc-800 overflow-hidden">
            {/* Hero metric row */}
            <div className="flex items-center justify-center gap-8 p-10 border-b border-zinc-800">
              <div className="flex items-center gap-4">
                <ShieldCheck
                  className="w-20 h-20 text-emerald-500"
                  strokeWidth={1.5}
                />
                <div>
                  <p className="text-4xl md:text-5xl font-bold text-emerald-400 font-mono">
                    0 Bytes
                  </p>
                  <p className="text-sm text-zinc-500 mt-1">
                    本月上传到云端的隐私数据
                  </p>
                </div>
              </div>
            </div>

            {/* Audit log - terminal style, rolling display */}
            <div className="p-6">
              <div className="rounded-lg bg-zinc-950 border border-zinc-800 p-4 font-mono text-sm min-h-[200px] overflow-hidden">
                <div className="text-zinc-600 mb-3">
                  $ privacy-audit --live
                </div>
                <div className="space-y-1.5">
                  {[...AUDIT_LOGS, ...AUDIT_LOGS]
                    .slice(logIndex, logIndex + 5)
                    .map((log, i) => (
                      <div
                        key={`${logIndex}-${i}`}
                        className="text-emerald-400/90"
                      >
                        {log}
                      </div>
                    ))}
                </div>
                <div className="mt-2 flex items-center gap-2">
                  <span className="w-2 h-4 bg-emerald-500/80 animate-pulse" />
                  <span className="text-emerald-500/60 text-xs">_</span>
                </div>
              </div>
            </div>
          </div>
        </section>
      </main>
    </div>
  );
}
