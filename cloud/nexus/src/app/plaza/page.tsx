"use client";

import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import Link from "next/link";
import Navbar from "@/components/Navbar";

// Mock: 边缘智能体自治日志 (Machine's Twitter)
const MOCK_LOGS = [
  "[1分钟前] 边缘智能体 0x8A9F 成功过滤 1000 条恶意指令",
  "[2分钟前] 蓝图 enterprise-legal-v1 被 Fork 23 次",
  "[3分钟前] 边缘智能体 0x3B2C 完成 RAG 索引更新",
  "[5分钟前] 新蓝图「傲娇女仆语音包」上架 Neural Market",
  "[8分钟前] 边缘智能体 0x7D1E 心跳正常，0 次上传",
  "[10分钟前] 魔法师 @prompt_mage 发布《企业级高管私人助理 v1.0》",
  "[12分钟前] 悬赏任务 #B042 已被极客接单",
  "[15分钟前] 边缘智能体 0x8A9F 成功过滤 1000 条恶意指令",
];

// Mock: 蓝图瀑布流数据
const MOCK_BLUEPRINTS = [
  { id: "1", name: "企业级高管私人助理 v1.0", deploys: 12500, author: "prompt_mage", avatar: "🧙", sbt: "金牌魔法师" },
  { id: "2", name: "低成本离线智慧门店方案", deploys: 8200, author: "edge_architect", avatar: "🏗️", sbt: "蓝图架构师" },
  { id: "3", name: "全自动 AI 心理医生", deploys: 5600, author: "flow_composer", avatar: "🎭", sbt: "灵魂注入者" },
  { id: "4", name: "傲娇女仆语音包", deploys: 18900, author: "vits_master", avatar: "🎤", sbt: "声纹雕刻师" },
  { id: "5", name: "少儿英语外教蓝图", deploys: 4200, author: "edu_wizard", avatar: "📚", sbt: "教育魔法师" },
  { id: "6", name: "自动挂断诈骗电话 AI 路由器", deploys: 3100, author: "security_geek", avatar: "🛡️", sbt: "防线守卫" },
];

function TickerTape({ logs }: { logs: string[] }) {
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
}: {
  blueprint: (typeof MOCK_BLUEPRINTS)[0];
  index: number;
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
          ⚡ 部署至边缘智能体
        </Link>
        <button
          type="button"
          className="flex-1 py-2 rounded-lg text-sm font-medium bg-purple-500/20 text-purple-400 border border-purple-400/40 hover:bg-purple-500/30 transition-colors"
        >
          🧬 一键 Fork
        </button>
      </motion.div>
    </motion.div>
  );
}

export default function PlazaPage() {
  const [logs] = useState(MOCK_LOGS);

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
            神经元广场
          </h1>
          <p className="text-white/50 text-sm">
            AI 时代的「魔法师抄作业」视觉盛宴 · 复合蓝图展示与一键 Fork
          </p>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6">
          {MOCK_BLUEPRINTS.map((bp, i) => (
            <BlueprintCard key={bp.id} blueprint={bp} index={i} />
          ))}
        </div>
      </main>
    </div>
  );
}
