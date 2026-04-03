"use client";

import { useState, useEffect, useCallback } from "react";
import Link from "next/link";
import ConsoleScaffold from "@/components/ConsoleScaffold";
import Toast from "@/components/Toast";
import { Rocket, ChevronDown, Loader2 } from "lucide-react";

interface FleetAgent {
  id: string;
  name: string;
  status: string;
  last_heartbeat: string | null;
  current_blueprint_id: string | null;
  blueprint_name: string;
}

interface Blueprint {
  id: string;
  name: string;
}

export default function FleetPage() {
  const [agents, setAgents] = useState<FleetAgent[]>([]);
  const [blueprints, setBlueprints] = useState<Blueprint[]>([]);
  const [stats, setStats] = useState({ online: 0, offline: 0, stale: 0, total: 0 });
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [deploying, setDeploying] = useState<Set<string>>(new Set());
  const [showDeployMenu, setShowDeployMenu] = useState(false);
  const [toastVisible, setToastVisible] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!showDeployMenu) return;
    const close = () => setShowDeployMenu(false);
    const t = setTimeout(() => document.addEventListener("click", close), 0);
    return () => {
      clearTimeout(t);
      document.removeEventListener("click", close);
    };
  }, [showDeployMenu]);

  const fetchFleet = useCallback(async () => {
    try {
      const res = await fetch("/api/v1/fleet");
      const data = await res.json();
      if (res.ok) {
        setAgents(data.agents ?? []);
        setBlueprints(data.blueprints ?? []);
        setStats(data.stats ?? { online: 0, offline: 0, stale: 0, total: 0 });
      }
    } catch {
      setAgents([]);
      setBlueprints([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchFleet();
    const id = setInterval(fetchFleet, 10000);
    return () => clearInterval(id);
  }, [fetchFleet]);

  const toggleSelect = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleSelectAll = () => {
    if (selected.size === agents.length) setSelected(new Set());
    else setSelected(new Set(agents.map((a) => a.id)));
  };

  const handleBulkDeploy = async (blueprintId: string) => {
    if (selected.size === 0) return;
    setShowDeployMenu(false);
    const ids = Array.from(selected);
    setDeploying(new Set(ids));
    try {
      const res = await fetch("/api/v1/fleet/deploy", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ agent_ids: ids, blueprint_id: blueprintId }),
      });
      const data = await res.json();
      if (res.ok && data.success) {
        setToastVisible(true);
        setSelected(new Set());
        await fetchFleet();
      }
    } finally {
      setDeploying(new Set());
    }
  };

  const isOnline = (a: FleetAgent) => a.status === "active";
  const isStale = (a: FleetAgent) => {
    if (a.status !== "active") return false;
    const hb = a.last_heartbeat ? new Date(a.last_heartbeat).getTime() : 0;
    return Date.now() - hb > 120000;
  };
  const formatHeartbeat = (hb: string | null) => {
    if (!hb) return "—";
    const d = new Date(hb);
    const diff = Date.now() - d.getTime();
    if (diff < 60000) return "刚刚";
    if (diff < 3600000) return `${Math.floor(diff / 60000)} 分钟前`;
    return d.toLocaleString();
  };

  return (
    <ConsoleScaffold>
      <main className="pt-20 px-6 pb-16 min-h-screen">
        <div className="max-w-7xl mx-auto">
          <div className="flex items-center justify-between mb-8">
            <h1 className="text-2xl font-bold tracking-widest text-cyan-400/95">
              舰队指挥大屏
            </h1>
            <Link
              href="/console"
              className="text-sm text-white/50 hover:text-cyan-400 transition-colors"
            >
              返回指挥台
            </Link>
          </div>

          {/* 统计面板 */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
            <div className="rounded-xl border border-green-500/30 bg-green-900/10 p-4">
              <p className="text-xs text-white/50 uppercase tracking-wider">在线突触</p>
              <p className="text-2xl font-bold text-green-400">{stats.online}</p>
            </div>
            <div className="rounded-xl border border-white/10 bg-black/30 p-4">
              <p className="text-xs text-white/50 uppercase tracking-wider">离线</p>
              <p className="text-2xl font-bold text-white/60">{stats.offline}</p>
            </div>
            <div className="rounded-xl border border-amber-500/20 bg-amber-900/5 p-4">
              <p className="text-xs text-white/50 uppercase tracking-wider">异常</p>
              <p className="text-2xl font-bold text-amber-400/80">{stats.stale}</p>
            </div>
            <div className="rounded-xl border border-cyan-500/20 bg-cyan-900/5 p-4">
              <p className="text-xs text-white/50 uppercase tracking-wider">总计</p>
              <p className="text-2xl font-bold text-cyan-400/90">{stats.total}</p>
            </div>
          </div>

          {/* 设备表格 */}
          <div className="rounded-2xl border border-white/10 bg-white/[0.02] overflow-hidden">
            {loading ? (
              <div className="flex items-center justify-center py-16">
                <Loader2 className="w-8 h-8 text-cyan-400 animate-spin" />
              </div>
            ) : agents.length === 0 ? (
              <div className="py-16 text-center text-white/50">
                <p className="mb-4">暂无边缘智能体接入</p>
                <Link
                  href="/console/pair"
                  className="inline-flex items-center gap-2 px-6 py-3 rounded-xl bg-cyan-500/20 border border-cyan-500/40 text-cyan-400 hover:bg-cyan-500/30 transition-colors"
                >
                  添加边缘智能体
                </Link>
              </div>
            ) : (
              <table className="w-full">
                <thead>
                  <tr className="border-b border-white/10">
                    <th className="text-left py-4 px-4 w-12">
                      <input
                        type="checkbox"
                        checked={selected.size === agents.length && agents.length > 0}
                        onChange={toggleSelectAll}
                        className="rounded border-white/30 bg-black/40 text-cyan-500 focus:ring-cyan-500"
                      />
                    </th>
                    <th className="text-left py-4 px-4 text-xs text-white/50 uppercase tracking-wider">设备名称</th>
                    <th className="text-left py-4 px-4 text-xs text-white/50 uppercase tracking-wider">状态</th>
                    <th className="text-left py-4 px-4 text-xs text-white/50 uppercase tracking-wider">最后心跳</th>
                    <th className="text-left py-4 px-4 text-xs text-white/50 uppercase tracking-wider">当前蓝图</th>
                  </tr>
                </thead>
                <tbody>
                  {agents.map((agent) => (
                    <tr
                      key={agent.id}
                      className="border-b border-white/5 hover:bg-white/[0.02] transition-colors"
                    >
                      <td className="py-4 px-4">
                        <input
                          type="checkbox"
                          checked={selected.has(agent.id)}
                          onChange={() => toggleSelect(agent.id)}
                          disabled={deploying.has(agent.id)}
                          className="rounded border-white/30 bg-black/40 text-cyan-500 focus:ring-cyan-500"
                        />
                      </td>
                      <td className="py-4 px-4 font-mono text-white/95">{agent.name}</td>
                      <td className="py-4 px-4">
                        <span className="flex items-center gap-2">
                          <span
                            className={`w-2 h-2 rounded-full ${
                              isOnline(agent)
                                ? isStale(agent)
                                  ? "bg-amber-500 animate-pulse"
                                  : "bg-green-500 animate-pulse"
                                : "bg-gray-600"
                            }`}
                          />
                          {deploying.has(agent.id) ? (
                            <span className="text-cyan-400/80 text-sm">🔄 更新中...</span>
                          ) : isOnline(agent) ? (
                            isStale(agent) ? "异常" : "在线"
                          ) : (
                            "离线"
                          )}
                        </span>
                      </td>
                      <td className="py-4 px-4 text-sm text-white/60">
                        {formatHeartbeat(agent.last_heartbeat)}
                      </td>
                      <td className="py-4 px-4 text-sm text-cyan-400 font-mono">
                        {agent.blueprint_name}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {/* 悬浮操作栏 */}
          {selected.size > 0 && (
            <div className="fixed bottom-8 left-1/2 -translate-x-1/2 z-50 flex items-center gap-4 px-6 py-4 rounded-2xl bg-slate-900/95 border border-cyan-500/30 shadow-[0_0_40px_rgba(34,211,238,0.15)] backdrop-blur-xl">
              <span className="text-sm text-white/80">
                已选 {selected.size} 台设备
              </span>
              <div className="relative">
                <button
                  onClick={(e) => { e.stopPropagation(); setShowDeployMenu(!showDeployMenu); }}
                  className="flex items-center gap-2 px-6 py-3 rounded-xl bg-cyan-500/20 border border-cyan-500/50 text-cyan-400 hover:bg-cyan-500/30 transition-colors font-medium"
                >
                  <Rocket className="w-5 h-5" />
                  批量下发蓝图
                  <ChevronDown className="w-4 h-4" />
                </button>
                {showDeployMenu && (
                  <div className="absolute bottom-full left-0 mb-2 w-56 rounded-xl border border-white/10 bg-slate-900 py-2 shadow-xl max-h-64 overflow-y-auto" onClick={(e) => e.stopPropagation()}>
                    {blueprints.length === 0 ? (
                      <p className="px-4 py-2 text-sm text-white/50">暂无蓝图</p>
                    ) : (
                      blueprints.map((bp) => (
                        <button
                          key={bp.id}
                          onClick={() => handleBulkDeploy(bp.id)}
                          className="w-full text-left px-4 py-2 text-sm text-white/90 hover:bg-cyan-500/20 hover:text-cyan-400 transition-colors"
                        >
                          {bp.name}
                        </button>
                      ))
                    )}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </main>

      <Toast
        message="舰队指令已全网同步！"
        visible={toastVisible}
        onClose={() => setToastVisible(false)}
      />
    </ConsoleScaffold>
  );
}
