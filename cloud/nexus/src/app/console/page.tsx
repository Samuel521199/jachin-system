"use client";

import { useState, useEffect, useCallback, useRef } from "react";
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
import ConsoleScaffold from "@/components/ConsoleScaffold";
import Toast from "@/components/Toast";
import { Stethoscope, MessageCircle, Shield, Activity } from "lucide-react";
import { useNexusUiLang } from "@/components/NexusUiLangProvider";
import {
  nexusConsole,
  nexusConsoleFallbackAgents,
  nexusConsoleFallbackBlueprints,
  readNexusUiLangFromStorage,
  type NexusUiLang,
} from "@/lib/nexus-ui-i18n";

const FALLBACK_ICONS = [Stethoscope, MessageCircle, Shield] as const;

function buildFallbackBlueprints(lang: NexusUiLang): Blueprint[] {
  const rows = nexusConsoleFallbackBlueprints[lang];
  return rows.map((bp, i) => ({
    id: bp.id,
    name: bp.name,
    icon: FALLBACK_ICONS[i] ?? MessageCircle,
    desc: bp.desc,
  }));
}

function buildFallbackAgents(lang: NexusUiLang) {
  const rows = nexusConsoleFallbackAgents[lang];
  const online = [true, true, false] as const;
  const cpu = [42, 18, 0] as const;
  const ram = [68, 45, 0] as const;
  return rows.map((a, i) => ({
    ...a,
    online: online[i] ?? false,
    cpu: cpu[i] ?? 0,
    ram: ram[i] ?? 0,
  }));
}

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
  ui,
}: {
  agent: Agent;
  isDeploying: boolean;
  ui: (typeof nexusConsole)[NexusUiLang];
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
            {ui.deploying}
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
            <p className="text-xs text-gray-400">{ui.currentBlueprint}</p>
            <p className="text-sm text-cyan-400 font-mono">{agent.blueprint}</p>
          </div>
        </>
      ) : (
        <div className="text-sm text-gray-500 py-4">{ui.offlineAgent}</div>
      )}
    </div>
  );
}

export default function ConsolePage() {
  const { lang } = useNexusUiLang();
  const nc = nexusConsole[lang];
  const apiBlueprintsLoaded = useRef(false);
  const apiAgentsFromFetch = useRef(false);
  /** 初始为空；仅在有接口数据或明确失败时使用 fallback，避免换账号后仍显示上一用户的节点 */
  const [agents, setAgents] = useState<Agent[]>(() => buildFallbackAgents(readNexusUiLangFromStorage()));
  const [blueprints, setBlueprints] = useState<Blueprint[]>(() =>
    buildFallbackBlueprints(readNexusUiLangFromStorage())
  );
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
          apiBlueprintsLoaded.current = true;
          setBlueprints(
            list.map((bp: { id: string; name: string; description: string }) => ({
              id: bp.id,
              name: bp.name,
              icon: MessageCircle,
              desc: bp.description || nc.forgeBlueprintDesc,
            }))
          );
        }
      } catch {
        // 保持 fallback
      }
    };
    fetchBlueprints();
  }, [nc.forgeBlueprintDesc]);

  useEffect(() => {
    const fetchInstances = async () => {
      try {
        const res = await fetch("/api/v1/instances", { credentials: "same-origin" });
        const data = await res.json();
        if (!res.ok) {
          if (res.status === 401) setAgents([]);
          return;
        }
        const list = data.instances ?? [];
        if (list.length > 0) apiAgentsFromFetch.current = true;
        setAgents((prev) =>
          list.map((inst: Record<string, unknown>, i: number) => {
            const id = String(inst.instance_id ?? `api-${i}`);
            const existing = prev.find((a) => a.id === id);
            const lastHb = inst.last_heartbeat as string | undefined;
            const status = inst.status as string | undefined;
            const online =
              status === "active" ||
              (lastHb ? Date.now() - new Date(lastHb).getTime() < 120000 : false);
            const metrics = inst.metrics as {
              cpu_percent?: number;
              ram_used_mb?: number;
              ram_total_mb?: number;
            } | undefined;
            const ramPct =
              metrics?.ram_total_mb && metrics?.ram_used_mb
                ? Math.round((metrics.ram_used_mb / metrics.ram_total_mb) * 100)
                : existing?.ram ?? 0;
            return {
              id,
              name: String(inst.name ?? inst.instance_id ?? nc.defaultAgentName),
              online,
              cpu: metrics?.cpu_percent ?? existing?.cpu ?? 0,
              ram: ramPct,
              blueprint: (inst.blueprint_name as string) ?? existing?.blueprint ?? "—",
            };
          })
        );
      } catch {
        setAgents(buildFallbackAgents(lang));
      }
    };
    fetchInstances();
    const interval = setInterval(fetchInstances, 10000);
    return () => clearInterval(interval);
  }, [lang, nc.defaultAgentName]);

  useEffect(() => {
    if (!apiBlueprintsLoaded.current) {
      setBlueprints(buildFallbackBlueprints(lang));
    }
  }, [lang]);

  useEffect(() => {
    if (!apiAgentsFromFetch.current) {
      setAgents(buildFallbackAgents(lang));
    }
  }, [lang]);

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
    <ConsoleScaffold>
      <DndContext onDragStart={handleDragStart} onDragEnd={handleDragEnd}>
        <main className="pt-20 px-6 pb-16 min-h-screen">
          <div className="flex gap-6 max-w-7xl mx-auto">
            {/* 左侧：蓝图武库 */}
            <aside className="w-64 flex-shrink-0">
              <div className="sticky top-24 rounded-2xl backdrop-blur-md bg-white/5 border border-white/10 p-4">
                <h2 className="text-sm font-semibold uppercase tracking-widest text-cyan-400/90 mb-4">
                  {nc.blueprintArmory}
                </h2>
                <p className="text-xs text-white/50 mb-4">
                  {nc.blueprintHint}
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
                  {nc.agentMapTitle}
                </h1>
                <div className="flex items-center gap-4">
                  <Link
                    href="/console/workspace"
                    className="text-sm text-white/50 hover:text-cyan-400 transition-colors"
                  >
                    {nc.linkWorkspace}
                  </Link>
                  <Link
                    href="/console/fleet"
                    className="text-sm text-cyan-400/80 hover:text-cyan-400 transition-colors"
                  >
                    {nc.linkFleet}
                  </Link>
                </div>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                {agents.map((agent) => (
                  <AgentCard
                    key={agent.id}
                    agent={agent}
                    isDeploying={deployingAgent === agent.id}
                    ui={nc}
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
        message={nc.toastDeployOk}
        visible={toastVisible}
        onClose={() => setToastVisible(false)}
      />
    </ConsoleScaffold>
  );
}
