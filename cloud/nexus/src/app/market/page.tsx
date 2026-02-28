"use client";

import { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import Navbar from "@/components/Navbar";

type SkillNode = {
  id: string;
  plugin_id: string;
  name: string;
  type: string;
  category: string;
  description: string;
  downloads: number;
  x: number;
  y: number;
  color: string;
  connections: string[];
};

const TARGET_INSTANCE_ID = "dev-layer2-instance-001";

export default function MarketPage() {
  const [selected, setSelected] = useState<SkillNode | null>(null);
  const [nodes, setNodes] = useState<SkillNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [deployLoading, setDeployLoading] = useState(false);
  const [deploySent, setDeploySent] = useState(false);

  useEffect(() => {
    fetch("/api/v1/plugins")
      .then((res) => res.json())
      .then((json) => {
        if (json.success && Array.isArray(json.data)) {
          const mapped = json.data.map((p: Record<string, unknown>) => ({
            id: (p.id as string) || (p.plugin_id as string),
            plugin_id: p.plugin_id as string,
            name: p.name as string,
            type: (p.category as string) || "skill",
            category: (p.category as string) || "skill",
            description: (p.description as string) || "",
            downloads: (p.download_count as number) ?? 0,
            x: (p.x as number) ?? 50,
            y: (p.y as number) ?? 50,
            color: (p.color as string) || "#6366f1",
            connections: (p.connections as string[]) || [],
          }));
          setNodes(mapped);
        }
      })
      .catch((err) => {
        console.error("Failed to fetch plugins:", err);
      })
      .finally(() => setLoading(false));
  }, []);

  const handleDeploy = async () => {
    if (!selected) return;
    setDeployLoading(true);
    setDeploySent(false);
    try {
      const res = await fetch("/api/v1/deploy", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          plugin_id: selected.plugin_id,
          target_instance_id: TARGET_INSTANCE_ID,
        }),
      });
      const json = await res.json();
      if (json.success) {
        setDeploySent(true);
      } else {
        console.error("Deploy failed:", json.error);
      }
    } catch (err) {
      console.error("Deploy error:", err);
    } finally {
      setDeployLoading(false);
    }
  };

  const handleSelectNode = (node: SkillNode) => {
    setSelected(node);
    setDeploySent(false);
  };

  return (
    <div className="min-h-screen relative">
      {/* Background */}
      <div
        className="fixed inset-0 -z-10"
        style={{
          background: `
            radial-gradient(ellipse 80% 50% at 50% 0%, rgba(88, 28, 135, 0.2) 0%, transparent 50%),
            radial-gradient(ellipse 60% 40% at 80% 60%, rgba(59, 7, 100, 0.12) 0%, transparent 50%),
            #050505
          `,
        }}
      />

      <Navbar />

      <div className="flex h-screen pt-16">
        {/* Left: 3D 神经元视图区 (70%) */}
        <div className="w-[70%] relative overflow-hidden">
          <div className="absolute top-6 left-8 z-10">
            <h1 className="text-sm font-medium tracking-widest text-white/50 uppercase">
              Neural Market
            </h1>
            <p className="text-xs text-white/30 mt-1">技能节点 · 点击探索</p>
          </div>
          <div className="absolute inset-0 flex items-center justify-center p-8">
            {loading ? (
              <div className="text-white/50">加载中...</div>
            ) : nodes.length === 0 ? (
              <div className="text-white/50">暂无插件</div>
            ) : (
              <svg
                viewBox="0 0 100 100"
                className="w-full h-full max-w-2xl max-h-[70vh]"
                preserveAspectRatio="xMidYMid meet"
              >
                {/* 连线 */}
                <g stroke="rgba(139, 92, 246, 0.2)" strokeWidth="0.3">
                  {nodes.flatMap((node) =>
                    node.connections.map((targetId) => {
                      const target = nodes.find((n) => n.id === targetId || n.plugin_id === targetId);
                      if (!target) return null;
                      return (
                        <line
                          key={`${node.id}-${targetId}`}
                          x1={node.x}
                          y1={node.y}
                          x2={target.x}
                          y2={target.y}
                          strokeDasharray="1 1"
                        />
                      );
                    })
                  )}
                </g>

                {/* 节点 */}
                {nodes.map((node) => (
                  <g key={node.id}>
                    <motion.circle
                      cx={node.x}
                      cy={node.y}
                      r={4}
                      fill={node.color}
                      filter="url(#glow)"
                      initial={{ scale: 1, opacity: 0.8 }}
                      animate={{
                        scale: [1, 1.15, 1],
                        opacity: [0.8, 1, 0.8],
                      }}
                      transition={{
                        duration: 2.5,
                        repeat: Infinity,
                        ease: "easeInOut",
                      }}
                    />
                    <motion.circle
                      cx={node.x}
                      cy={node.y}
                      r={2.2}
                      fill={node.color}
                      className="cursor-pointer"
                      onClick={() => handleSelectNode(node)}
                      whileHover={{ scale: 1.4 }}
                      whileTap={{ scale: 1.1 }}
                      style={{
                        filter: `drop-shadow(0 0 6px ${node.color})`,
                      }}
                    />
                  </g>
                ))}
                {nodes.map((node) => (
                  <text
                    key={`label-${node.id}`}
                    x={node.x}
                    y={node.y - 5}
                    textAnchor="middle"
                    fill="rgba(255,255,255,0.5)"
                    style={{ fontSize: 2.8, fontFamily: "monospace" }}
                  >
                    {node.name}
                  </text>
                ))}
                <defs>
                  <filter id="glow" x="-50%" y="-50%" width="200%" height="200%">
                    <feGaussianBlur stdDeviation="1" result="blur" />
                    <feMerge>
                      <feMergeNode in="blur" />
                      <feMergeNode in="SourceGraphic" />
                    </feMerge>
                  </filter>
                </defs>
              </svg>
            )}
          </div>
        </div>

        {/* Right: 详情面板 (30%) */}
        <div className="w-[30%] min-w-[280px] relative">
          <AnimatePresence mode="wait">
            {selected ? (
              <motion.div
                key="detail"
                initial={{ x: 40, opacity: 0 }}
                animate={{ x: 0, opacity: 1 }}
                exit={{ x: 40, opacity: 0 }}
                transition={{ type: "spring", damping: 25, stiffness: 300 }}
                className="h-full p-6 backdrop-blur-xl bg-black/30 border-l border-white/10"
              >
                <div className="flex items-center gap-2 mb-4">
                  <span
                    className="w-2 h-2 rounded-full"
                    style={{ backgroundColor: selected.color }}
                  />
                  <span className="text-xs uppercase tracking-wider text-white/50">
                    {selected.category === "persona" ? "Persona" : "Skill"}
                  </span>
                </div>
                <h2 className="text-xl font-semibold text-white mb-3">
                  {selected.name}
                </h2>
                <p className="text-sm text-white/60 leading-relaxed mb-6">
                  {selected.description || "暂无描述"}
                </p>
                <div className="text-sm text-white/40 mb-8">
                  <span className="font-mono">{selected.downloads.toLocaleString()}</span> 次部署
                </div>
                <button
                  onClick={handleDeploy}
                  disabled={deployLoading}
                  className={`
                    w-full py-3.5 rounded-lg font-medium text-white
                    border transition-all duration-300
                    disabled:opacity-60 disabled:cursor-not-allowed
                    ${deploySent
                      ? "bg-emerald-500/30 border-emerald-400/50"
                      : "bg-violet-500/30 hover:bg-violet-500/50 border-violet-400/50 animate-pulse-glow"
                    }
                  `}
                >
                  {deployLoading
                    ? "部署中..."
                    : deploySent
                    ? "Deploy Command Sent"
                    : "Deploy to Layer 2"}
                </button>
                <button
                  onClick={() => setSelected(null)}
                  className="mt-4 w-full py-2 text-sm text-white/40 hover:text-white/70 transition-colors"
                >
                  关闭
                </button>
              </motion.div>
            ) : (
              <motion.div
                key="placeholder"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="h-full flex flex-col items-center justify-center p-8 text-center backdrop-blur-xl bg-black/20 border-l border-white/5"
              >
                <div className="w-16 h-16 rounded-full border border-dashed border-white/20 flex items-center justify-center mb-4">
                  <span className="text-2xl text-white/30">◇</span>
                </div>
                <p className="text-white/40 text-sm mb-2">点击左侧节点</p>
                <p className="text-white/25 text-xs">查看技能详情并部署到私有大脑</p>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>
    </div>
  );
}
