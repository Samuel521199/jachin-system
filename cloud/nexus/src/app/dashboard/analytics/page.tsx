"use client";

import { useState, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  LineChart,
  Line,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import Navbar from "@/components/Navbar";
import { Activity, TrendingUp, Zap, CheckCircle2 } from "lucide-react";

const DEMO_TENANT_ID = "demo-tenant-001";

type UsagePoint = { hour?: number; day?: number; calls: number; label: string };
type SkillItem = { item_id: string; name: string; calls: number };
type GlobalStats = { successRate: number; avgLatencyMs: number };

export default function AnalyticsDashboardPage() {
  const [range, setRange] = useState<"24h" | "7d">("24h");
  const [usageTrend, setUsageTrend] = useState<UsagePoint[]>([]);
  const [skillRanking, setSkillRanking] = useState<SkillItem[]>([]);
  const [globalStats, setGlobalStats] = useState<GlobalStats>({ successRate: 100, avgLatencyMs: 0 });
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (typeof document === "undefined") return;
    const hasTenant = document.cookie.includes("nexus_tenant_id=");
    if (!hasTenant) {
      document.cookie = `nexus_tenant_id=${encodeURIComponent(DEMO_TENANT_ID)}; path=/; max-age=31536000`;
    }
  }, []);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch(
        `/api/v1/analytics/tenant?range=${range}`,
        { credentials: "include" }
      );
      const json = await res.json();
      if (json.success) {
        setUsageTrend(json.usageTrend ?? []);
        setSkillRanking(json.skillRanking ?? []);
        setGlobalStats(json.globalStats ?? { successRate: 100, avgLatencyMs: 0 });
      }
    } catch {
      setUsageTrend([]);
      setSkillRanking([]);
      setGlobalStats({ successRate: 100, avgLatencyMs: 0 });
    } finally {
      setLoading(false);
    }
  }, [range]);

  useEffect(() => {
    fetchData();
    const t = setInterval(fetchData, 60000);
    return () => clearInterval(t);
  }, [fetchData]);

  const totalCalls = usageTrend.reduce((s, p) => s + p.calls, 0);

  return (
    <div className="min-h-screen relative overflow-hidden">
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
            radial-gradient(ellipse 100% 80% at 50% 0%, rgba(34, 211, 238, 0.08) 0%, transparent 50%),
            radial-gradient(ellipse 80% 60% at 100% 80%, rgba(139, 92, 246, 0.06) 0%, transparent 50%),
            radial-gradient(ellipse 60% 50% at 0% 0%, rgba(34, 211, 238, 0.05) 0%, transparent 50%)
          `,
        }}
      />

      <Navbar />

      <main className="pt-24 pb-16 px-6 max-w-7xl mx-auto">
        <header className="mb-10 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold text-white tracking-tight mb-1">
              企业主审计大屏
            </h1>
            <p className="text-white/50 text-sm font-mono">
              用量趋势 · 活跃技能 · 全局成功率
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setRange("24h")}
              className={`px-4 py-2 rounded-lg text-sm font-medium transition-all ${
                range === "24h"
                  ? "bg-cyan-500/30 text-cyan-300 border border-cyan-400/50"
                  : "bg-white/5 text-white/60 border border-white/10 hover:bg-white/10"
              }`}
            >
              24 小时
            </button>
            <button
              onClick={() => setRange("7d")}
              className={`px-4 py-2 rounded-lg text-sm font-medium transition-all ${
                range === "7d"
                  ? "bg-cyan-500/30 text-cyan-300 border border-cyan-400/50"
                  : "bg-white/5 text-white/60 border border-white/10 hover:bg-white/10"
              }`}
            >
              7 天
            </button>
          </div>
        </header>

        <AnimatePresence mode="wait">
          {loading ? (
            <motion.div
              key="loading"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="grid gap-6 md:grid-cols-2 lg:grid-cols-3"
            >
              {[1, 2, 3].map((i) => (
                <motion.div
                  key={i}
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: i * 0.1 }}
                  className="h-64 rounded-xl border border-white/10 bg-white/[0.02] animate-pulse"
                />
              ))}
            </motion.div>
          ) : (
            <motion.div
              key="content"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.3 }}
              className="space-y-6"
            >
              {/* 数据卡片 */}
              <motion.div
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                className="grid gap-4 sm:grid-cols-3"
              >
                <div className="rounded-xl border border-cyan-400/20 bg-cyan-500/5 p-5 backdrop-blur-sm">
                  <div className="flex items-center gap-3">
                    <div className="rounded-lg bg-cyan-500/20 p-2">
                      <Activity className="h-5 w-5 text-cyan-400" />
                    </div>
                    <div>
                      <p className="text-white/50 text-xs font-medium uppercase tracking-wider">
                        总调用量
                      </p>
                      <p className="text-2xl font-bold text-white tabular-nums">
                        {totalCalls.toLocaleString()}
                      </p>
                    </div>
                  </div>
                </div>
                <div className="rounded-xl border border-violet-400/20 bg-violet-500/5 p-5 backdrop-blur-sm">
                  <div className="flex items-center gap-3">
                    <div className="rounded-lg bg-violet-500/20 p-2">
                      <Zap className="h-5 w-5 text-violet-400" />
                    </div>
                    <div>
                      <p className="text-white/50 text-xs font-medium uppercase tracking-wider">
                        活跃技能
                      </p>
                      <p className="text-2xl font-bold text-white tabular-nums">
                        {skillRanking.length}
                      </p>
                    </div>
                  </div>
                </div>
                <div className="rounded-xl border border-emerald-400/20 bg-emerald-500/5 p-5 backdrop-blur-sm">
                  <div className="flex items-center gap-3">
                    <div className="rounded-lg bg-emerald-500/20 p-2">
                      <CheckCircle2 className="h-5 w-5 text-emerald-400" />
                    </div>
                    <div>
                      <p className="text-white/50 text-xs font-medium uppercase tracking-wider">
                        全局成功率 / 平均耗时
                      </p>
                      <p className="text-2xl font-bold text-white tabular-nums">
                        {globalStats.successRate}% / {globalStats.avgLatencyMs}ms
                      </p>
                    </div>
                  </div>
                </div>
              </motion.div>

              {/* 用量趋势 */}
              <motion.div
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: 0.1 }}
                className="rounded-xl border border-white/10 bg-white/[0.02] p-6 backdrop-blur-sm"
              >
                <h3 className="text-sm font-medium text-white/80 mb-4 flex items-center gap-2">
                  <TrendingUp className="h-4 w-4 text-cyan-400" />
                  用量趋势
                </h3>
                <div className="h-72">
                  {usageTrend.length > 0 ? (
                    <ResponsiveContainer width="100%" height="100%">
                      <LineChart data={usageTrend} margin={{ top: 5, right: 20, left: 0, bottom: 5 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                        <XAxis
                          dataKey="label"
                          stroke="rgba(255,255,255,0.4)"
                          tick={{ fontSize: 11 }}
                        />
                        <YAxis
                          stroke="rgba(255,255,255,0.4)"
                          tick={{ fontSize: 11 }}
                          tickFormatter={(v) => v.toLocaleString()}
                        />
                        <Tooltip
                          contentStyle={{
                            background: "rgba(10,10,15,0.95)",
                            border: "1px solid rgba(255,255,255,0.1)",
                            borderRadius: "8px",
                          }}
                          labelStyle={{ color: "#22d3ee" }}
                          formatter={(value) => [
                            (typeof value === "number" ? value : 0).toLocaleString(),
                            "调用量",
                          ]}
                        />
                        <Line
                          type="monotone"
                          dataKey="calls"
                          stroke="#22d3ee"
                          strokeWidth={2}
                          dot={{ fill: "#22d3ee", strokeWidth: 0 }}
                          activeDot={{ r: 4, fill: "#22d3ee", stroke: "#fff", strokeWidth: 2 }}
                        />
                      </LineChart>
                    </ResponsiveContainer>
                  ) : (
                    <div className="h-full flex items-center justify-center text-white/30 text-sm">
                      暂无数据
                    </div>
                  )}
                </div>
              </motion.div>

              {/* 活跃技能排名 - 全宽 */}
              <motion.div
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: 0.2 }}
                className="rounded-xl border border-white/10 bg-white/[0.02] p-6 backdrop-blur-sm"
              >
                <h3 className="text-sm font-medium text-white/80 mb-4 flex items-center gap-2">
                  <Zap className="h-4 w-4 text-violet-400" />
                  活跃技能排名
                </h3>
                <div className="h-72">
                  {skillRanking.length > 0 ? (
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart
                        data={skillRanking}
                        layout="vertical"
                        margin={{ top: 5, right: 30, left: 0, bottom: 5 }}
                      >
                        <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                        <XAxis type="number" stroke="rgba(255,255,255,0.4)" tick={{ fontSize: 10 }} />
                        <YAxis
                          type="category"
                          dataKey="name"
                          width={120}
                          stroke="rgba(255,255,255,0.4)"
                          tick={{ fontSize: 10 }}
                        />
                        <Tooltip
                          contentStyle={{
                            background: "rgba(10,10,15,0.95)",
                            border: "1px solid rgba(255,255,255,0.1)",
                            borderRadius: "8px",
                          }}
                          formatter={(value) => [
                            (typeof value === "number" ? value : 0).toLocaleString(),
                            "调用",
                          ]}
                        />
                        <Bar dataKey="calls" fill="#a78bfa" radius={[0, 4, 4, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  ) : (
                    <div className="h-full flex items-center justify-center text-white/30 text-sm">
                      暂无数据
                    </div>
                  )}
                </div>
              </motion.div>
            </motion.div>
          )}
        </AnimatePresence>
      </main>
    </div>
  );
}
