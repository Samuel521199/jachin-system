/**
 * Jachin Link - 网络拓扑与设备列表（HUD 风格）
 */

import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import { Network, Wifi, WifiOff, RefreshCw } from "lucide-react";
import { getDevices, DeviceStatus } from "../../lib/api";

const TOPO_SIZE = 160;
const HUB_R = 14;
const DEVICE_R = 10;
const RADIUS = 52;

function devicePosition(index: number, total: number): { x: number; y: number } {
  if (total <= 0) return { x: TOPO_SIZE / 2, y: TOPO_SIZE / 2 };
  const step = (2 * Math.PI) / total;
  const angle = -Math.PI / 2 + index * step;
  const cx = TOPO_SIZE / 2;
  const cy = TOPO_SIZE / 2;
  return {
    x: cx + RADIUS * Math.cos(angle),
    y: cy + RADIUS * Math.sin(angle),
  };
}

function DeviceTopologyStrip({ devices }: { devices: DeviceStatus[] }) {
  const cx = TOPO_SIZE / 2;
  const cy = TOPO_SIZE / 2;

  return (
    <div className="flex-shrink-0 flex items-center gap-4 py-4 px-4 rounded-xl bg-black/30 border border-white/10 mb-6">
      <span className="text-[10px] uppercase tracking-wider text-slate-500 font-mono flex-shrink-0">
        拓扑
      </span>
      <div className="flex-1 min-w-0 flex items-center justify-center">
        <svg
          viewBox={`0 0 ${TOPO_SIZE} ${TOPO_SIZE}`}
          className="w-full max-w-[180px] h-auto"
          aria-label="设备拓扑"
        >
          <defs>
            <filter id="glow-link">
              <feGaussianBlur stdDeviation="1.5" result="blur" />
              <feMerge>
                <feMergeNode in="blur" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
          </defs>
          {/* Hub → 各设备连线 */}
          {devices.map((d, i) => {
            const pos = devicePosition(i, devices.length);
            const online = d.status === "online";
            const len = Math.hypot(pos.x - cx, pos.y - cy);
            return (
              <g key={d.deviceId}>
                {/* 静态虚线 */}
                <line
                  x1={cx}
                  y1={cy}
                  x2={pos.x}
                  y2={pos.y}
                  stroke={online ? "rgba(34, 211, 238, 0.25)" : "rgba(255,255,255,0.08)"}
                  strokeWidth="1"
                  strokeDasharray={online ? "0" : "4 3"}
                />
                {/* 在线设备：数据流动画 */}
                {online && (
                  <line
                    x1={cx}
                    y1={cy}
                    x2={pos.x}
                    y2={pos.y}
                    stroke="rgba(34, 211, 238, 0.6)"
                    strokeWidth="1.5"
                    strokeLinecap="round"
                    strokeDasharray={`${len} ${len}`}
                    filter="url(#glow-link)"
                  >
                    <animate
                      attributeName="stroke-dashoffset"
                      from={len}
                      to={0}
                      dur="2s"
                      repeatCount="indefinite"
                    />
                  </line>
                )}
              </g>
            );
          })}
          {/* 设备节点 */}
          {devices.map((d, i) => {
            const pos = devicePosition(i, devices.length);
            const online = d.status === "online";
            return (
              <g key={d.deviceId}>
                <circle
                  cx={pos.x}
                  cy={pos.y}
                  r={DEVICE_R}
                  fill={online ? "rgba(34, 211, 238, 0.3)" : "rgba(255,255,255,0.06)"}
                  stroke={online ? "rgba(34, 211, 238, 0.7)" : "rgba(255,255,255,0.15)"}
                  strokeWidth="1.5"
                />
                <title>{`${d.name} · ${d.status}`}</title>
              </g>
            );
          })}
          {/* 中心 Hub */}
          <circle
            cx={cx}
            cy={cy}
            r={HUB_R}
            fill="rgba(244, 63, 94, 0.15)"
            stroke="rgba(244, 63, 94, 0.5)"
            strokeWidth="2"
          />
          <text
            x={cx}
            y={cy + 1}
            textAnchor="middle"
            style={{ fontSize: "9px", fill: "rgba(251, 113, 133, 0.95)" }}
          >
            Hub
          </text>
        </svg>
      </div>
      <span className="text-[10px] text-slate-500 font-mono flex-shrink-0">
        {devices.length} 设备 · {devices.filter((d) => d.status === "online").length} 在线
      </span>
    </div>
  );
}

export function JachinLink() {
  const [devices, setDevices] = useState<DeviceStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showOffline, setShowOffline] = useState(false);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const list = await getDevices(!showOffline);
      setDevices(list);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, [showOffline]);

  return (
    <div className="h-full flex flex-col p-6 overflow-auto">
      <header className="flex-shrink-0 flex items-center justify-between mb-6">
        <div>
          <h1
            className="font-sci-fi text-2xl font-bold text-transparent bg-clip-text bg-gradient-to-r from-rose-400 to-rose-600"
            style={{ fontFamily: "Orbitron, sans-serif" }}
          >
            Jachin Link
          </h1>
          <p className="text-slate-500 text-sm mt-0.5">网络拓扑与已连接设备</p>
        </div>
        <button
          type="button"
          onClick={load}
          disabled={loading}
          title="刷新设备列表"
          className="p-2 rounded-lg glass-panel border border-white/10 hover:border-rose-500/30 disabled:opacity-50 transition-colors"
        >
          <RefreshCw className={`w-5 h-5 text-slate-400 ${loading ? "animate-spin" : ""}`} />
        </button>
      </header>

      <DeviceTopologyStrip devices={devices} />

      <motion.section
        className="flex-1 min-h-0 glass-panel rounded-xl overflow-hidden flex flex-col"
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        <div className="flex-shrink-0 px-4 py-3 border-b border-white/10 flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <Network className="w-4 h-4 text-rose-400/80" />
            <span className="font-mono text-xs uppercase tracking-wider text-slate-400">
              已连接设备
            </span>
          </div>
          <button
            type="button"
            onClick={() => setShowOffline((v) => !v)}
            className="text-xs text-slate-500 hover:text-slate-400 font-mono"
          >
            {showOffline ? "仅在线" : "含离线"}
          </button>
        </div>
        <div className="flex-1 overflow-auto p-4 min-h-0">
          {error && (
            <p className="text-amber-400 text-sm font-mono mb-4">{error}</p>
          )}
          {loading && devices.length === 0 ? (
            <div className="flex items-center gap-2 text-slate-400 py-12 font-mono text-sm">
              <RefreshCw className="w-5 h-5 animate-spin" />
              加载中...
            </div>
          ) : devices.length === 0 ? (
            <div className="text-center py-12 text-slate-500 font-mono text-sm">
              <p>暂无设备</p>
              <p className="mt-1 text-slate-600">设备连接后将显示在这里</p>
            </div>
          ) : (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {devices.map((device, i) => (
                <motion.div
                  key={device.deviceId}
                  className="rounded-xl border border-white/10 bg-white/5 p-4 hover:bg-white/10 hover:border-rose-500/20 transition-colors"
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: i * 0.03 }}
                >
                  <div className="flex items-center justify-between mb-2">
                    <span className="font-mono text-sm font-medium text-white truncate">
                      {device.name}
                    </span>
                    {device.status === "online" ? (
                      <Wifi className="w-4 h-4 text-cyan-400 flex-shrink-0" />
                    ) : (
                      <WifiOff className="w-4 h-4 text-slate-500 flex-shrink-0" />
                    )}
                  </div>
                  <div className="text-[11px] text-slate-500 font-mono truncate" title={device.deviceId}>
                    {device.deviceId}
                  </div>
                  {device.capabilities.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1">
                      {device.capabilities.slice(0, 5).map((c) => (
                        <span
                          key={c}
                          className="px-2 py-0.5 rounded bg-rose-500/20 text-rose-300/90 text-[10px] font-mono"
                        >
                          {c}
                        </span>
                      ))}
                      {device.capabilities.length > 5 && (
                        <span className="text-slate-500 text-[10px] font-mono">
                          +{device.capabilities.length - 5}
                        </span>
                      )}
                    </div>
                  )}
                </motion.div>
              ))}
            </div>
          )}
        </div>
      </motion.section>
    </div>
  );
}
