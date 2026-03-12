"use client";

import { useState, useEffect, useCallback } from "react";
import Link from "next/link";
import {
  DndContext,
  DragOverlay,
  useDraggable,
  useDroppable,
  type DragEndEvent,
  type DragStartEvent,
} from "@dnd-kit/core";
import { CSS } from "@dnd-kit/utilities";
import { motion } from "framer-motion";
import Navbar from "@/components/Navbar";
import Toast from "@/components/Toast";
import { Stethoscope, MessageCircle, Shield, Activity } from "lucide-react";

const FALLBACK_BLUEPRINTS = [
  { id: "bp-1", name: "离线医疗助手", icon: Stethoscope, desc: "本地诊断推理" },
  { id: "bp-2", name: "傲娇女仆客服", icon: MessageCircle, desc: "语音对话服务" },
  { id: "bp-3", name: "安防视觉中枢", icon: Shield, desc: "实时视频分析" },
];

const FALLBACK_AGENTS = [
  { id: "agent-1", name: "多伦多一号机", online: true, cpu: 42, ram: 68, blueprint: "离线医疗助手" },
  { id: "agent-2", name: "树莓派测试节点", online: true, cpu: 18, ram: 45, blueprint: "傲娇女仆客服" },
  { id: "agent-3", name: "上海门店终端", online: false, cpu: 0, ram: 0, blueprint: "—" },
];

interface Blueprint {
  id: string;
  name: string;
  icon: React.ComponentType<{ className?: string }>;
  desc: string;
}

interface Agent {
  id: string;
  name: string;
  online: boolean;
  cpu: number;
  ram: number;
  blueprint: string;
}

function BlueprintCard({
  blueprint,
  isDragging,
}: {
  blueprint: Blueprint;
  isDragging?: boolean;
}) {
  const Icon = blueprint.icon;
  const { attributes, listeners, setNodeRef, transform } = useDraggable({
    id: blueprint.id,
    data: { type: "blueprint", blueprint },
  });
  const style = transform ? { transform: CSS.Translate.toString(transform) } : undefined;

  return (
    <div
      ref={setNodeRef}
      style={style}
      {...listeners}
      {...attributes}
      className={`
        p-4 rounded-xl border cursor-grab active:cursor-grabbing
        bg-black/40 border-white/10 hover:border-cyan-500/40 hover:shadow-[0_0_20px_rgba(34,211,238,0.15)]
        transition-all select-none
        ${isDragging ? "opacity-50" : ""}
      `}
    >
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 rounded-lg bg-cyan-500/20 flex items-center justify-center">
          <Icon className="w-5 h-5 text-cyan-400" />
        </div>
        <div>
          <p className="font-medium text-white/95">{blueprint.name}</p>
          <p className="text-xs text-white/50">{blueprint.desc}</p>
        </div>
      </div>
    </div>
  );
}

function AgentCard({
  agent,
  isDeploying,
}: {
  agent: Agent;
  isDeploying: boolean;
}) {
  const { setNodeRef, isOver } = useDroppable({
    id: agent.id,
    data: { type: "agent", agent },
  });
  const showHighlight = isOver;

  return (
    <div
      ref={setNodeRef}
      className={`
        relative p-6 rounded-2xl backdrop-blur-xl border transition-all min-h-[180px]
        ${agent.online ? "border-green-500/30 bg-green-900/10" : "border-white/10 bg-black/30"}
        ${showHighlight ? "ring-2 ring-cyan-500 ring-offset-2 ring-offset-[#050505]" : ""}
      `}
    >
      {isDeploying && (
        <div className="absolute inset-0 rounded-2xl bg-black/80 backdrop-blur-sm flex flex-col items-center justify-center z-10">
          <Activity className="w-10 h-10 text-cyan-400 animate-pulse mb-2" />
          <p className="text-cyan-400 text-sm animate-pulse">
            📡 正在热更新蓝图... (Deploying Blueprint...)
          </p>
        </div>
      )}

      <div className="flex justify-between items-center mb-4">
        <h3 className="text-lg font-mono text-white/95">{agent.name}</h3>
        <span
          className={`w-3 h-3 rounded-full ${
            agent.online ? "bg-green-500 animate-pulse" : "bg-gray-600"
          }`}
        />
      </div>

      {agent.online ? (
        <>
          <div className="space-y-2 mb-4">
            <div className="flex justify-between text-xs text-white/60">
              <span>CPU</span>
              <span>{agent.cpu}%</span>
            </div>
            <div className="h-1.5 rounded-full bg-white/10 overflow-hidden">
              <motion.div
                className="h-full bg-cyan-500/80 rounded-full"
                initial={{ width: 0 }}
                animate={{ width: `${agent.cpu}%` }}
                transition={{ duration: 0.5 }}
              />
            </div>
            <div className="flex justify-between text-xs text-white/60">
              <span>RAM</span>
              <span>{agent.ram}%</span>
            </div>
            <div className="h-1.5 rounded-full bg-white/10 overflow-hidden">
              <motion.div
                className="h-full bg-purple-500/80 rounded-full"
                initial={{ width: 0 }}
                animate={{ width: `${agent.ram}%` }}
                transition={{ duration: 0.5 }}
              />
            </div>
          </div>
          <div className="border-t border-white/10 pt-3">
            <p className="text-xs text-gray-400">当前蓝图</p>
            <p className="text-sm text-cyan-400 font-mono">{agent.blueprint}</p>
          </div>
        </>
      ) : (
        <div className="text-sm text-gray-500 py-4">边缘智能体已离线</div>
      )}
    </div>
  );
}

export default function ConsolePage() {
  const [agents, setAgents] = useState<Agent[]>(FALLBACK_AGENTS);
  const [blueprints, setBlueprints] = useState<Blueprint[]>(FALLBACK_BLUEPRINTS);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [deployingAgent, setDeployingAgent] = useState<string | null>(null);
  const [toastVisible, setToastVisible] = useState(false);

  useEffect(() => {
    const fetchBlueprints = async () => {
      try {
        const res = await fetch("/api/v1/blueprints");
        const data = await res.json();
        const list = data.blueprints ?? [];
        if (list.length > 0) {
          setBlueprints(
            list.map((bp: { id: string; name: string; description: string }) => ({
              id: bp.id,
              name: bp.name,
              icon: MessageCircle,
              desc: bp.description || "Forge 蓝图",
            }))
          );
        }
      } catch {
        // 保持 fallback
      }
    };
    fetchBlueprints();
  }, []);

  useEffect(() => {
    const fetchInstances = async () => {
      try {
        const res = await fetch("/api/v1/instances");
        const data = await res.json();
        const list = data.instances ?? [];
        if (list.length > 0) {
          setAgents((prev) =>
            list.map((inst: Record<string, unknown>, i: number) => {
              const id = String(inst.instance_id ?? `api-${i}`);
              const existing = prev.find((a) => a.id === id);
              const lastHb = inst.last_heartbeat as string | undefined;
              const status = inst.status as string | undefined;
              const online =
                status === "active" ||
                (lastHb ? Date.now() - new Date(lastHb).getTime() < 120000 : false);
              const metrics = inst.metrics as { cpu_percent?: number; ram_used_mb?: number; ram_total_mb?: number } | undefined;
              const ramPct =
                metrics?.ram_total_mb && metrics?.ram_used_mb
                  ? Math.round((metrics.ram_used_mb / metrics.ram_total_mb) * 100)
                  : existing?.ram ?? 0;
              return {
                id,
                name: String(inst.name ?? inst.instance_id ?? "边缘智能体"),
                online,
                cpu: metrics?.cpu_percent ?? existing?.cpu ?? 0,
                ram: ramPct,
                blueprint: (inst.blueprint_name as string) ?? existing?.blueprint ?? "—",
              };
            })
          );
        }
      } catch {
        // 保持 fallback
      }
    };
    fetchInstances();
    const interval = setInterval(fetchInstances, 10000);
    return () => clearInterval(interval);
  }, []);

  const handleDragStart = useCallback((event: DragStartEvent) => {
    setActiveId(String(event.active.id));
  }, []);

  const handleDragEnd = useCallback((event: DragEndEvent) => {
    setActiveId(null);
    const { active, over } = event;
    if (!over || active.id === over.id) return;

    const activeData = active.data.current;
    const overData = over.data.current;
    if (activeData?.type !== "blueprint" || overData?.type !== "agent") return;

    const blueprint = activeData.blueprint as Blueprint;
    const agent = overData.agent as Agent;
    if (!agent.online) return;

    setDeployingAgent(agent.id);
    setTimeout(() => {
      setAgents((prev) =>
        prev.map((a) =>
          a.id === agent.id ? { ...a, blueprint: blueprint.name } : a
        )
      );
      setDeployingAgent(null);
      setToastVisible(true);
    }, 2000);
  }, []);

  const activeBlueprint = activeId
    ? blueprints.find((b) => b.id === activeId)
    : null;

  return (
    <div className="min-h-screen bg-[#050505]">
      <div
        className="fixed inset-0 -z-10 pointer-events-none opacity-30"
        style={{
          backgroundImage: `url("data:image/svg+xml,%3Csvg width='60' height='60' viewBox='0 0 60 60' xmlns='http://www.w3.org/2000/svg'%3E%3Cg fill='none' fill-rule='evenodd'%3E%3Cg fill='%2322d3ee' fill-opacity='0.08'%3E%3Cpath d='M36 34v-4h-2v4h-4v2h4v4h2v-4h4v-2h-4zm0-30V0h-2v4h-4v2h4v4h2V6h4V4h-4zM6 34v-4H4v4H0v2h4v4h2v-4h4v-2H6zM6 4V0H4v4H0v2h4v4h2V6h4V4H6z'/%3E%3C/g%3E%3C/g%3E%3C/svg%3E")`,
        }}
      />
      <div
        className="fixed inset-0 -z-10 pointer-events-none"
        style={{
          background: `
            radial-gradient(ellipse 60% 40% at 50% 20%, rgba(34, 211, 238, 0.06) 0%, transparent 50%),
            radial-gradient(ellipse 40% 60% at 80% 80%, rgba(168, 85, 247, 0.04) 0%, transparent 50%),
            #050505
          `,
        }}
      />

      <Navbar />

      <DndContext onDragStart={handleDragStart} onDragEnd={handleDragEnd}>
        <main className="pt-20 px-6 pb-16 min-h-screen">
          <div className="flex gap-6 max-w-7xl mx-auto">
            {/* 左侧：蓝图武库 */}
            <aside className="w-64 flex-shrink-0">
              <div className="sticky top-24 rounded-2xl backdrop-blur-md bg-white/5 border border-white/10 p-4">
                <h2 className="text-sm font-semibold uppercase tracking-widest text-cyan-400/90 mb-4">
                  蓝图武库
                </h2>
                <p className="text-xs text-white/50 mb-4">
                  拖拽到右侧智能体卡片上完成部署
                </p>
                <div className="space-y-3">
                  {blueprints.map((bp) => (
                    <BlueprintCard
                      key={bp.id}
                      blueprint={bp}
                      isDragging={activeId === bp.id}
                    />
                  ))}
                </div>
              </div>
            </aside>

            {/* 右侧：边缘智能体星图 */}
            <section className="flex-1 min-w-0">
              <div className="flex items-center justify-between mb-6">
                <h1 className="text-2xl font-bold tracking-widest text-cyan-400/95">
                  边缘智能体星图
                </h1>
                <Link
                  href="/console/fleet"
                  className="text-sm text-cyan-400/80 hover:text-cyan-400 transition-colors"
                >
                  舰队指挥大屏 →
                </Link>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                {agents.map((agent) => (
                  <AgentCard
                    key={agent.id}
                    agent={agent}
                    isDeploying={deployingAgent === agent.id}
                  />
                ))}
              </div>
            </section>
          </div>
        </main>

        <DragOverlay>
          {activeBlueprint ? (
            <div className="p-4 rounded-xl border bg-black/90 border-cyan-500/50 shadow-[0_0_30px_rgba(34,211,238,0.3)] cursor-grabbing">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-lg bg-cyan-500/20 flex items-center justify-center">
                  <activeBlueprint.icon className="w-5 h-5 text-cyan-400" />
                </div>
                <div>
                  <p className="font-medium text-white">{activeBlueprint.name}</p>
                  <p className="text-xs text-white/60">{activeBlueprint.desc}</p>
                </div>
              </div>
            </div>
          ) : null}
        </DragOverlay>
      </DndContext>

      <Toast
        message="✅ 蓝图已成功下发至边缘智能体！"
        visible={toastVisible}
        onClose={() => setToastVisible(false)}
      />
    </div>
  );
}
