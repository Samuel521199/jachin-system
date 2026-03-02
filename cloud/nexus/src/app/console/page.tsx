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

interface InstanceMetrics {
  cpu_percent?: number;
  ram_used_mb?: number;
  ram_total_mb?: number;
}

interface Instance {
  instance_id: string;
  core_version?: string;
  last_heartbeat?: string;
  metrics?: InstanceMetrics;
  active_plugins?: Record<string, string>;
}

export default function ConsolePage() {
  const [instances, setInstances] = useState<Instance[]>([]);
  const [logIndex, setLogIndex] = useState(0);

  // 每 10 秒刷新大屏数据
  useEffect(() => {
    const fetchInstances = async () => {
      try {
        const res = await fetch("/api/v1/instances");
        const data = await res.json();
        setInstances(data.instances ?? []);
      } catch {
        setInstances([]);
      }
    };
    fetchInstances();
    const interval = setInterval(fetchInstances, 10000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    const id = setInterval(() => {
      setLogIndex((i) => (i + 1) % AUDIT_LOGS.length);
    }, 2500);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="min-h-screen bg-[#050505]">
      <div
        className="fixed inset-0 -z-10 pointer-events-none"
        style={{
          background: `
            radial-gradient(ellipse 60% 40% at 50% 20%, rgba(168, 85, 247, 0.08) 0%, transparent 50%),
            radial-gradient(ellipse 40% 60% at 80% 80%, rgba(34, 197, 94, 0.04) 0%, transparent 50%),
            #050505
          `,
        }}
      />

      <Navbar />

      <main className="pt-20 px-6 pb-16 max-w-6xl mx-auto">
        {/* 舰队指挥台大屏 */}
        <section className="mb-16">
          <h1 className="text-3xl font-bold tracking-widest text-purple-400 mb-8">
            舰队指挥台
          </h1>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {instances.map((instance) => {
              const lastHb = instance.last_heartbeat
                ? new Date(instance.last_heartbeat).getTime()
                : 0;
              const isOnline =
                lastHb > 0 && Date.now() - lastHb < 60000;

              return (
                <div
                  key={instance.instance_id}
                  className={`p-6 rounded-2xl backdrop-blur-xl border transition-all ${
                    isOnline
                      ? "border-green-500/30 bg-green-900/10"
                      : "border-white/10 bg-black/30"
                  }`}
                >
                  <div className="flex justify-between items-center mb-4">
                    <h3 className="text-xl font-mono">
                      {instance.instance_id.split("-").slice(0, 2).join("-").toUpperCase()} 边缘智能体
                    </h3>
                    <span
                      className={`w-3 h-3 rounded-full ${
                        isOnline ? "bg-green-500 animate-pulse" : "bg-gray-600"
                      }`}
                    />
                  </div>

                  {isOnline ? (
                    <>
                      <div className="text-sm text-cyan-400 mb-2">
                        CPU: {instance.metrics?.cpu_percent ?? "?"}% | RAM:{" "}
                        {instance.metrics?.ram_used_mb ?? "?"}MB
                        {instance.metrics?.ram_total_mb != null &&
                          ` / ${instance.metrics.ram_total_mb}MB`}
                      </div>
                      <div className="text-xs text-zinc-500 mb-4">
                        v{instance.core_version ?? "?"}
                      </div>
                      <div className="mt-4 border-t border-white/10 pt-4">
                        <h4 className="text-xs text-gray-400 mb-2">
                          装载武器库:
                        </h4>
                        {instance.active_plugins &&
                        Object.keys(instance.active_plugins).length > 0 ? (
                          Object.entries(instance.active_plugins).map(
                            ([plugin, state]) => (
                              <div
                                key={plugin}
                                className="flex items-center text-xs mt-1 font-mono"
                              >
                                {state === "running" && (
                                  <span className="text-green-400 mr-2">
                                    [运行]
                                  </span>
                                )}
                                {state === "restarting" && (
                                  <span className="text-yellow-400 mr-2 animate-pulse">
                                    [抢救中]
                                  </span>
                                )}
                                {state === "fatal" && (
                                  <span className="text-red-500 mr-2">
                                    [炸膛]
                                  </span>
                                )}
                                {state === "stopped" && (
                                  <span className="text-gray-500 mr-2">
                                    [已停]
                                  </span>
                                )}
                                {!["running", "restarting", "fatal", "stopped"].includes(
                                  state
                                ) && (
                                  <span className="text-amber-400/80 mr-2">
                                    [{state}]
                                  </span>
                                )}
                                <span className="text-gray-300">
                                  {plugin}
                                </span>
                              </div>
                            )
                          )
                        ) : (
                          <div className="text-xs text-zinc-600">
                            暂无插件
                          </div>
                        )}
                      </div>
                    </>
                  ) : (
                    <div className="text-sm text-gray-500 mt-4">
                      边缘智能体已丢失连接...
                    </div>
                  )}
                </div>
              );
            })}
          </div>
          {instances.length === 0 && (
            <div className="text-center py-12 text-zinc-500">
              暂无边缘智能体接入，等待前线信号...
            </div>
          )}
        </section>

        {/* Privacy Audit */}
        <section className="mt-20">
          <h2 className="text-xs font-semibold uppercase tracking-widest text-zinc-500 mb-6">
            Privacy Audit
          </h2>
          <div className="rounded-2xl bg-zinc-900/80 border border-zinc-800 overflow-hidden">
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
