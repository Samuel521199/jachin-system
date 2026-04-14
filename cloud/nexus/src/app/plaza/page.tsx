"use client";

import { useState } from "react";
import { motion } from "framer-motion";
import Link from "next/link";
import Navbar from "@/components/Navbar";
import { useNexusUiLang } from "@/components/NexusUiLangProvider";
import {
  nexusPlaza,
  nexusPlazaMockBlueprints,
  nexusPlazaMockLogs,
  type NexusUiLang,
} from "@/lib/nexus-ui-i18n";

type PlazaBlueprint = (typeof nexusPlazaMockBlueprints)[NexusUiLang][number];

function TickerTape({ logs }: { logs: readonly string[] }) {
  const duplicated = [...logs, ...logs];
  return (
    <div className="overflow-hidden border-y border-cyan-500/20 bg-black/40 backdrop-blur-sm">
      <motion.div
        className="flex whitespace-nowrap py-2"
        animate={{ x: ["0%", "-50%"] }}
        transition={{ duration: 25, repeat: Infinity, ease: "linear" }}
      >
        {duplicated.map((log, i) => (
          <span key={i} className="mx-8 text-sm text-cyan-400/90 font-mono">
            {log}
          </span>
        ))}
      </motion.div>
    </div>
  );
}

function BlueprintCard({
  blueprint,
  index,
  deployLabel,
  forkLabel,
}: {
  blueprint: PlazaBlueprint;
  index: number;
  deployLabel: string;
  forkLabel: string;
}) {
  const [hovered, setHovered] = useState(false);
  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.05 }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      className={`
        relative rounded-xl border bg-white/5 backdrop-blur-md p-5
        transition-all duration-300
        ${hovered ? "border-cyan-400/50 shadow-[0_0_30px_rgba(34,211,238,0.2)] -translate-y-1" : "border-white/10"}
      `}
    >
      <h3 className="text-lg font-semibold text-white/95 mb-2">{blueprint.name}</h3>
      <p className="text-cyan-400/80 text-sm mb-3 font-mono">🔥 {blueprint.deploys.toLocaleString()} Deploys</p>
      <div className="flex items-center gap-2 mb-4">
        <span className="w-8 h-8 rounded-full bg-white/10 flex items-center justify-center text-lg">
          {blueprint.avatar}
        </span>
        <div>
          <p className="text-white/80 text-sm">@{blueprint.author}</p>
          <span className="text-xs text-purple-400/80 px-1.5 py-0.5 rounded bg-purple-500/20 border border-purple-400/30">
            {blueprint.sbt}
          </span>
        </div>
      </div>
      <motion.div
        initial={false}
        animate={{ opacity: hovered ? 1 : 0, y: hovered ? 0 : 8 }}
        className="flex gap-2"
      >
        <Link
          href={`/market?deploy=${blueprint.id}`}
          className="flex-1 py-2 rounded-lg text-center text-sm font-medium bg-cyan-500/20 text-cyan-400 border border-cyan-400/40 hover:bg-cyan-500/30 transition-colors"
        >
          {deployLabel}
        </Link>
        <button
          type="button"
          className="flex-1 py-2 rounded-lg text-sm font-medium bg-purple-500/20 text-purple-400 border border-purple-400/40 hover:bg-purple-500/30 transition-colors"
        >
          {forkLabel}
        </button>
      </motion.div>
    </motion.div>
  );
}

export default function PlazaPage() {
  const { lang } = useNexusUiLang();
  const t = nexusPlaza[lang];
  const logs = nexusPlazaMockLogs[lang];
  const mockBlueprints = nexusPlazaMockBlueprints[lang];

  return (
    <div className="min-h-screen relative overflow-hidden">
      {/* 全局背景：深空暗黑 + 网格线 + 径向渐变 */}
      <div
        className="fixed inset-0 -z-10"
        style={{
          background: `
            radial-gradient(ellipse 80% 50% at 50% 0%, rgba(34, 211, 238, 0.08) 0%, transparent 50%),
            radial-gradient(ellipse 60% 40% at 80% 60%, rgba(139, 92, 246, 0.06) 0%, transparent 50%),
            linear-gradient(rgba(255,255,255,0.02) 1px, transparent 1px),
            linear-gradient(90deg, rgba(255,255,255,0.02) 1px, transparent 1px),
            #050505
          `,
          backgroundSize: "100% 100%, 100% 100%, 40px 40px, 40px 40px",
        }}
      />

      <Navbar />

      {/* 顶部：边缘智能体自治日志 (Machine's Twitter) */}
      <div className="pt-16">
        <TickerTape logs={logs} />
      </div>

      {/* 核心区域：蓝图瀑布流 */}
      <main className="max-w-7xl mx-auto px-6 py-12">
        <div className="mb-10">
          <h1 className="text-3xl font-bold text-white/95 tracking-tight mb-2">
            {t.title}
          </h1>
          <p className="text-white/50 text-sm">
            {t.subtitle}
          </p>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6">
          {mockBlueprints.map((bp, i) => (
            <BlueprintCard
              key={bp.id}
              blueprint={bp}
              index={i}
              deployLabel={t.deploy}
              forkLabel={t.fork}
            />
          ))}
        </div>
      </main>
    </div>
  );
}
