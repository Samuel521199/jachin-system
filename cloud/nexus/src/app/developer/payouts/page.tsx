"use client";

import { useState, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import Navbar from "@/components/Navbar";
import { useNexusUiLang } from "@/components/NexusUiLangProvider";
import { nexusDeveloperPayouts } from "@/lib/nexus-ui-i18n";
import { Activity, Package, CheckCircle2, Clock } from "lucide-react";

const DEMO_DEVELOPER_ID = "dev-demo-001";

type AppItem = {
  item_id: string;
  name: string;
  total_calls: number;
  unpaid_amount_cents: number;
  success_rate: number;
  avg_latency_ms: number;
};

export default function DeveloperPayoutsPage() {
  const { lang } = useNexusUiLang();
  const t = nexusDeveloperPayouts[lang];
  const [totalCalls, setTotalCalls] = useState(0);
  const [unpaidAmountCents, setUnpaidAmountCents] = useState(0);
  const [paidAmountCents, setPaidAmountCents] = useState(0);
  const [appList, setAppList] = useState<AppItem[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (typeof document === "undefined") return;
    const hasDev = document.cookie.includes("nexus_developer_id=");
    if (!hasDev) {
      document.cookie = `nexus_developer_id=${encodeURIComponent(DEMO_DEVELOPER_ID)}; path=/; max-age=31536000`;
    }
  }, []);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch("/api/v1/analytics/developer", {
        credentials: "include",
      });
      const json = await res.json();
      if (json.success) {
        setTotalCalls(json.totalCalls ?? 0);
        setUnpaidAmountCents(json.unpaidAmountCents ?? 0);
        setPaidAmountCents(json.paidAmountCents ?? 0);
        setAppList(json.appList ?? []);
      }
    } catch {
      setTotalCalls(0);
      setUnpaidAmountCents(0);
      setPaidAmountCents(0);
      setAppList([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
    const t = setInterval(fetchData, 60000);
    return () => clearInterval(t);
  }, [fetchData]);

  const unpaidYuan = (unpaidAmountCents / 100).toFixed(2);
  const paidYuan = (paidAmountCents / 100).toFixed(2);

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
            radial-gradient(ellipse 100% 80% at 50% 0%, rgba(34, 211, 238, 0.06) 0%, transparent 50%),
            radial-gradient(ellipse 80% 60% at 100% 80%, rgba(139, 92, 246, 0.08) 0%, transparent 50%),
            radial-gradient(ellipse 60% 50% at 0% 100%, rgba(236, 72, 153, 0.05) 0%, transparent 50%)
          `,
        }}
      />

      <Navbar />

      <main className="pt-24 pb-16 px-6 max-w-5xl mx-auto">
        <header className="mb-10">
          <h1 className="text-2xl font-bold text-white tracking-tight mb-1">
            {t.title}
          </h1>
          <p className="text-white/50 text-sm font-mono">
            {t.subtitle}
          </p>
        </header>

        <AnimatePresence mode="wait">
          {loading ? (
            <motion.div
              key="loading"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="space-y-6"
            >
              <div className="grid gap-4 sm:grid-cols-3">
                {[1, 2, 3].map((i) => (
                  <div
                    key={i}
                    className="h-28 rounded-xl border border-white/10 bg-white/[0.02] animate-pulse"
                  />
                ))}
              </div>
              <div className="h-80 rounded-xl border border-white/10 bg-white/[0.02] animate-pulse" />
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
              <div className="grid gap-4 sm:grid-cols-3">
                <motion.div
                  initial={{ opacity: 0, y: 20 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="rounded-xl border border-cyan-400/20 bg-cyan-500/5 p-5 backdrop-blur-sm"
                >
                  <div className="flex items-center gap-3">
                    <div className="rounded-lg bg-cyan-500/20 p-2">
                      <Activity className="h-5 w-5 text-cyan-400" />
                    </div>
                    <div>
                      <p className="text-white/50 text-xs font-medium uppercase tracking-wider">
                        {t.totalCalls}
                      </p>
                      <p className="text-2xl font-bold text-white tabular-nums">
                        {totalCalls.toLocaleString()}
                      </p>
                    </div>
                  </div>
                </motion.div>
                <motion.div
                  initial={{ opacity: 0, y: 20 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: 0.05 }}
                  className="rounded-xl border border-amber-400/20 bg-amber-500/5 p-5 backdrop-blur-sm"
                >
                  <div className="flex items-center gap-3">
                    <div className="rounded-lg bg-amber-500/20 p-2">
                      <Clock className="h-5 w-5 text-amber-400" />
                    </div>
                    <div>
                      <p className="text-white/50 text-xs font-medium uppercase tracking-wider">
                        {t.pendingBalance}
                      </p>
                      <p className="text-2xl font-bold text-white tabular-nums">
                        ¥{unpaidYuan}
                      </p>
                      <p className="text-xs text-white/40 mt-0.5">
                        {t.rateHint}
                      </p>
                    </div>
                  </div>
                </motion.div>
                <motion.div
                  initial={{ opacity: 0, y: 20 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: 0.1 }}
                  className="rounded-xl border border-emerald-400/20 bg-emerald-500/5 p-5 backdrop-blur-sm"
                >
                  <div className="flex items-center gap-3">
                    <div className="rounded-lg bg-emerald-500/20 p-2">
                      <CheckCircle2 className="h-5 w-5 text-emerald-400" />
                    </div>
                    <div>
                      <p className="text-white/50 text-xs font-medium uppercase tracking-wider">
                        {t.settled}
                      </p>
                      <p className="text-2xl font-bold text-white tabular-nums">
                        ¥{paidYuan}
                      </p>
                    </div>
                  </div>
                </motion.div>
              </div>

              {/* 应用列表 */}
              <motion.div
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: 0.15 }}
                className="rounded-xl border border-white/10 bg-white/[0.02] overflow-hidden backdrop-blur-sm"
              >
                <div className="p-4 border-b border-white/10 flex items-center gap-2">
                  <Package className="h-4 w-4 text-violet-400" />
                  <h3 className="text-sm font-medium text-white/80">
                    {t.appList}
                  </h3>
                </div>
                <div className="overflow-x-auto">
                  {appList.length > 0 ? (
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b border-white/10">
                          <th className="text-left py-3 px-4 text-white/60 font-medium">
                            {t.colApp}
                          </th>
                          <th className="text-right py-3 px-4 text-white/60 font-medium">
                            {t.colCalls}
                          </th>
                          <th className="text-right py-3 px-4 text-white/60 font-medium">
                            {t.colPending}
                          </th>
                          <th className="text-right py-3 px-4 text-white/60 font-medium">
                            {t.colSuccess}
                          </th>
                          <th className="text-right py-3 px-4 text-white/60 font-medium">
                            {t.colLatency}
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {appList.map((app, i) => (
                          <motion.tr
                            key={app.item_id}
                            initial={{ opacity: 0, x: -10 }}
                            animate={{ opacity: 1, x: 0 }}
                            transition={{ delay: 0.2 + i * 0.05 }}
                            className="border-b border-white/5 hover:bg-white/[0.02] transition-colors"
                          >
                            <td className="py-3 px-4">
                              <span className="font-medium text-white/90">
                                {app.name}
                              </span>
                              <span className="text-white/40 text-xs ml-2 font-mono">
                                {app.item_id}
                              </span>
                            </td>
                            <td className="text-right py-3 px-4 tabular-nums text-white/80">
                              {app.total_calls.toLocaleString()}
                            </td>
                            <td className="text-right py-3 px-4 tabular-nums text-amber-400">
                              ¥{(app.unpaid_amount_cents / 100).toFixed(2)}
                            </td>
                            <td className="text-right py-3 px-4 tabular-nums">
                              <span
                                className={
                                  app.success_rate >= 95
                                    ? "text-emerald-400"
                                    : app.success_rate >= 80
                                      ? "text-amber-400"
                                      : "text-red-400"
                                }
                              >
                                {app.success_rate}%
                              </span>
                            </td>
                            <td className="text-right py-3 px-4 tabular-nums text-white/70">
                              {app.avg_latency_ms.toFixed(0)} ms
                            </td>
                          </motion.tr>
                        ))}
                      </tbody>
                    </table>
                  ) : (
                    <div className="p-12 text-center text-white/40 text-sm">
                      {t.empty}
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
