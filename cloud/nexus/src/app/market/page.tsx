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
  manifest_json?: Record<string, unknown> | null;
};

const CATEGORY_LABELS: Record<string, string> = {
  skill: "左脑能力",
  persona: "右脑灵魂",
  memory: "海马体记忆",
  default: "插件",
};

/** 贝塞尔曲线：原生 App 级滑入减速质感 */
const EASE_SMOOTH = [0.16, 1, 0.3, 1];

function ManifestCodeBlock({ manifest }: { manifest: Record<string, unknown> | null }) {
  if (!manifest) return null;
  const str = JSON.stringify(manifest, null, 2);
  return (
    <pre className="text-[11px] leading-relaxed text-cyan-300/90 font-mono overflow-x-auto overflow-y-auto max-h-40 p-3 rounded-lg bg-black/40 border border-white/10">
      <code>{str}</code>
    </pre>
  );
}

export default function MarketPage() {
  const [selected, setSelected] = useState<SkillNode | null>(null);
  const [hoveredNode, setHoveredNode] = useState<SkillNode | null>(null);
  const [nodes, setNodes] = useState<SkillNode[]>([]);
  const [instances, setInstances] = useState<{ instance_id: string }[]>([]);
  const [targetInstanceId, setTargetInstanceId] = useState("dev-layer2-instance-001");
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
            manifest_json: (p.manifest_json as Record<string, unknown>) ?? null,
          }));
          setNodes(mapped);
        }
      })
      .catch((err) => console.error("Failed to fetch plugins:", err))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    fetch("/api/v1/instances")
      .then((res) => res.json())
      .then((json) => {
        if (json.instances?.length) {
          setInstances(json.instances);
          setTargetInstanceId((prev) => prev || json.instances[0]?.instance_id || "dev-layer2-instance-001");
        }
      })
      .catch(() => {});
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
          target_instance_id: targetInstanceId,
        }),
      });
      const json = await res.json();
      if (json.success) setDeploySent(true);
      else console.error("Deploy failed:", json.error);
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

  const manifestDesc =
    selected?.manifest_json &&
    (typeof selected.manifest_json.description === "string"
      ? selected.manifest_json.description
      : selected.description);

  return (
    <div className="min-h-screen relative overflow-hidden">
      {/* 深空星图网格背景 */}
      <div
        className="fixed inset-0 -z-10"
        style={{
          background: `
            linear-gradient(rgba(255,255,255,0.02) 1px, transparent 1px),
            linear-gradient(90deg, rgba(255,255,255,0.02) 1px, transparent 1px)
          `,
          backgroundSize: "48px 48px",
          backgroundColor: "#030712",
        }}
      />
      <div
        className="fixed inset-0 -z-10"
        style={{
          background: `
            radial-gradient(ellipse 100% 80% at 50% 20%, rgba(34, 211, 238, 0.06) 0%, transparent 50%),
            radial-gradient(ellipse 80% 60% at 80% 70%, rgba(244, 114, 182, 0.05) 0%, transparent 50%),
            radial-gradient(ellipse 60% 50% at 20% 60%, rgba(167, 139, 250, 0.05) 0%, transparent 50%)
          `,
        }}
      />

      <Navbar />

      <div className="flex h-screen pt-16">
        {/* 左：神经元星图 */}
        <div className="w-[70%] relative overflow-hidden">
          <div className="absolute top-6 left-8 z-10">
            <h1 className="text-sm font-medium tracking-[0.2em] text-white/40 uppercase">
              Neural Market
            </h1>
            <p className="text-xs text-white/25 mt-1">悬浮在深空中的赛博朋克神经元星图</p>
          </div>
          <div className="absolute inset-0 flex items-center justify-center p-8">
            {loading ? (
              <div className="text-white/40 animate-pulse">加载神经元...</div>
            ) : nodes.length === 0 ? (
              <div className="text-white/40">暂无插件</div>
            ) : (
              <svg
                viewBox="0 0 100 100"
                className="w-full h-full max-w-2xl max-h-[70vh]"
                preserveAspectRatio="xMidYMid meet"
              >
                <g stroke="rgba(255,255,255,0.06)" strokeWidth="0.2">
                  {nodes.flatMap((node) =>
                    node.connections.map((targetId) => {
                      const target = nodes.find(
                        (n) => n.id === targetId || n.plugin_id === targetId
                      );
                      if (!target) return null;
                      return (
                        <line
                          key={`${node.id}-${targetId}`}
                          x1={node.x}
                          y1={node.y}
                          x2={target.x}
                          y2={target.y}
                          strokeDasharray="1 1"
                          stroke={node.color}
                          opacity={hoveredNode?.id === node.id || hoveredNode?.id === target.id ? 0.6 : 0.25}
                        />
                      );
                    })
                  )}
                </g>
                {nodes.map((node) => {
                  const isHovered = hoveredNode?.id === node.id;
                  return (
                    <g
                      key={node.id}
                      onMouseEnter={() => setHoveredNode(node)}
                      onMouseLeave={() => setHoveredNode(null)}
                    >
                      {/* 外圈光晕 - 悬停时散发类型光晕 */}
                      <motion.circle
                        cx={node.x}
                        cy={node.y}
                        r={isHovered ? 8 : 5}
                        fill="none"
                        stroke={node.color}
                        strokeWidth={0.5}
                        opacity={isHovered ? 0.6 : 0.2}
                        animate={{ opacity: isHovered ? 0.6 : 0.2 }}
                        transition={{ duration: 0.2 }}
                      />
                      <motion.circle
                        cx={node.x}
                        cy={node.y}
                        r={4}
                        fill={node.color}
                        filter="url(#glow)"
                        animate={{
                          scale: isHovered ? 1.3 : 1,
                          opacity: isHovered ? 1 : 0.85,
                        }}
                        transition={{ duration: 0.2 }}
                      />
                      <motion.circle
                        cx={node.x}
                        cy={node.y}
                        r={2.5}
                        fill={node.color}
                        className="cursor-pointer"
                        onClick={() => handleSelectNode(node)}
                        animate={{
                          scale: isHovered ? 1.4 : 1,
                          filter: isHovered
                            ? `drop-shadow(0 0 16px ${node.color}) drop-shadow(0 0 32px ${node.color}80)`
                            : `drop-shadow(0 0 6px ${node.color}60)`,
                        }}
                        transition={{ duration: 0.2 }}
                      />
                    </g>
                  );
                })}
                {nodes.map((node) => (
                  <text
                    key={`label-${node.id}`}
                    x={node.x}
                    y={node.y - 6.5}
                    textAnchor="middle"
                    fill={hoveredNode?.id === node.id ? "rgba(255,255,255,0.9)" : "rgba(255,255,255,0.5)"}
                    style={{ fontSize: 2.5, fontFamily: "monospace" }}
                  >
                    {node.name}
                  </text>
                ))}
                <defs>
                  <filter id="glow" x="-100%" y="-100%" width="300%" height="300%">
                    <feGaussianBlur stdDeviation="1.5" result="blur" />
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

        {/* 右：毛玻璃详情面板 - cubic-bezier 滑入 */}
        <div className="w-[30%] min-w-[320px] relative">
          <AnimatePresence mode="wait">
            {selected ? (
              <motion.div
                key="detail"
                initial={{ x: 80, opacity: 0 }}
                animate={{ x: 0, opacity: 1 }}
                exit={{ x: 80, opacity: 0 }}
                transition={{
                  duration: 0.5,
                  ease: EASE_SMOOTH as [number, number, number, number],
                }}
                className="h-full p-6 bg-black/60 backdrop-blur-3xl border-l border-white/10 shadow-[0_0_80px_rgba(0,0,0,0.5)]"
              >
                <div className="flex items-center gap-2 mb-4">
                  <span
                    className="w-2.5 h-2.5 rounded-full"
                    style={{
                      backgroundColor: selected.color,
                      boxShadow: `0 0 16px ${selected.color}, 0 0 32px ${selected.color}60`,
                    }}
                  />
                  <span className="text-xs uppercase tracking-widest text-white/50">
                    {CATEGORY_LABELS[selected.category] || selected.category}
                  </span>
                </div>
                <h2 className="text-xl font-semibold text-white mb-3 tracking-tight">
                  {selected.name}
                </h2>
                <p className="text-sm text-white/70 leading-relaxed mb-5">
                  {manifestDesc || selected.description || "暂无描述"}
                </p>
                <div className="text-sm text-white/40 mb-5">
                  <span className="font-mono">{selected.downloads.toLocaleString()}</span> 次部署
                </div>

                {/* JMP 2.0 Manifest 代码块 */}
                <div className="mb-5">
                  <p className="text-xs text-white/40 mb-2 font-mono">manifest.json</p>
                  <ManifestCodeBlock manifest={selected.manifest_json ?? null} />
                </div>

                {instances.length > 0 && (
                  <div className="mb-4">
                    <label className="text-xs text-white/50 block mb-2">部署目标</label>
                    <select
                      value={targetInstanceId}
                      onChange={(e) => setTargetInstanceId(e.target.value)}
                      className="w-full py-2.5 px-3 rounded-lg bg-black/40 border border-white/10 text-white text-sm focus:outline-none focus:ring-1 focus:ring-white/30 focus:border-white/20 transition-colors"
                    >
                      {instances.map((i) => (
                        <option key={i.instance_id} value={i.instance_id} className="bg-zinc-900">
                          {i.instance_id}
                        </option>
                      ))}
                    </select>
                  </div>
                )}
                <button
                  onClick={handleDeploy}
                  disabled={deployLoading}
                  className={`
                    w-full py-3.5 rounded-lg font-medium text-white
                    border transition-all duration-300
                    disabled:opacity-60 disabled:cursor-not-allowed
                    ${deploySent
                      ? "bg-emerald-500/30 border-emerald-400/50 shadow-[0_0_20px_rgba(52,211,153,0.2)]"
                      : "bg-violet-500/30 hover:bg-violet-500/50 border-violet-400/50 hover:shadow-[0_0_24px_rgba(139,92,246,0.3)]"
                    }
                  `}
                >
                  {deployLoading
                    ? "部署中..."
                    : deploySent
                    ? "✓ 指令已下发"
                    : "部署到边缘智能体"}
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
                transition={{ duration: 0.3, ease: EASE_SMOOTH as [number, number, number, number] }}
                className="h-full flex flex-col items-center justify-center p-8 text-center bg-black/60 backdrop-blur-3xl border-l border-white/5"
              >
                <div
                  className="w-20 h-20 rounded-full border border-dashed border-white/15 flex items-center justify-center mb-5"
                  style={{ boxShadow: "inset 0 0 30px rgba(255,255,255,0.02)" }}
                >
                  <span className="text-3xl text-white/25">◇</span>
                </div>
                <p className="text-white/40 text-sm mb-2">点击左侧神经元节点</p>
                <p className="text-white/25 text-xs max-w-[200px]">
                  查看 JMP 2.0 Manifest 并部署到私有大脑
                </p>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>
    </div>
  );
}
