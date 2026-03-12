/**
 * PerformanceDashboard - 性能监控仪表盘组件
 * 
 * 显示系统性能指标，包括：
 * - 插件执行性能
 * - 意图规划性能
 * - LLM 调用性能
 * - 错误率统计
 */

import React, { useState, useEffect } from "react";
import { motion } from "framer-motion";
import { Activity, AlertTriangle, TrendingUp, Clock, XCircle } from "lucide-react";
import { cn } from "../../utils/cn";
import { BACKEND_URL } from "../../lib/api";

interface PerformanceStats {
  [key: string]: {
    count: number;
    avg_time: number;
    min_time: number;
    max_time: number;
    total_time: number;
    errors: number;
    error_rate: number;
  };
}

interface PerformanceDashboardProps {
  className?: string;
  refreshInterval?: number; // 刷新间隔（毫秒）
}

export const PerformanceDashboard: React.FC<PerformanceDashboardProps> = ({
  className,
  refreshInterval = 5000, // 默认 5 秒刷新
}) => {
  const [stats, setStats] = useState<PerformanceStats>({});
  const [alerts, setAlerts] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchStats = async () => {
    try {
      const response = await fetch(`${BACKEND_URL}/api/v3/monitoring/stats`);
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const data = await response.json();
      setStats(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "获取性能统计失败");
    } finally {
      setLoading(false);
    }
  };

  const fetchAlerts = async () => {
    try {
      const response = await fetch(`${BACKEND_URL}/api/v3/monitoring/alerts`);
      if (!response.ok) {
        return;
      }
      const data = await response.json();
      setAlerts(data.alerts || []);
    } catch (err) {
      // 忽略告警获取错误
    }
  };

  useEffect(() => {
    fetchStats();
    fetchAlerts();
    
    const interval = setInterval(() => {
      fetchStats();
      fetchAlerts();
    }, refreshInterval);

    return () => clearInterval(interval);
  }, [refreshInterval]);

  const formatTime = (seconds: number) => {
    if (seconds < 1) {
      return `${(seconds * 1000).toFixed(0)}ms`;
    }
    return `${seconds.toFixed(2)}s`;
  };

  const getMetricColor = (metricName: string) => {
    if (metricName.includes("error") || metricName.includes("failure")) {
      return "text-red-400";
    }
    if (metricName.includes("plugin")) {
      return "text-purple-400";
    }
    if (metricName.includes("intent")) {
      return "text-blue-400";
    }
    if (metricName.includes("llm")) {
      return "text-green-400";
    }
    return "text-gray-400";
  };

  if (loading) {
    return (
      <div className={cn("p-4", className)}>
        <div className="text-gray-400">加载中...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className={cn("p-4", className)}>
        <div className="text-red-400">错误: {error}</div>
      </div>
    );
  }

  return (
    <div className={cn("space-y-4", className)}>
      {/* 告警区域 */}
      {alerts.length > 0 && (
        <motion.div
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          className="bg-red-900/20 border border-red-500/30 rounded-lg p-4"
        >
          <div className="flex items-center gap-2 mb-2">
            <AlertTriangle className="w-5 h-5 text-red-400" />
            <h3 className="text-lg font-semibold text-red-300">告警</h3>
          </div>
          <div className="space-y-2">
            {alerts.map((alert, index) => (
              <div key={index} className="text-sm text-red-200">
                {alert.message}
              </div>
            ))}
          </div>
        </motion.div>
      )}

      {/* 性能指标卡片 */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {Object.entries(stats).map(([metricName, metricStats]) => (
          <motion.div
            key={metricName}
            initial={{ opacity: 0, scale: 0.95 }}
            animate={{ opacity: 1, scale: 1 }}
            className={cn(
              "bg-gray-900/50 border border-purple-500/20 rounded-lg p-4",
              "backdrop-blur-sm"
            )}
          >
            {/* 指标标题 */}
            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                <Activity className={cn("w-4 h-4", getMetricColor(metricName))} />
                <h4 className="text-sm font-semibold text-white">
                  {metricName.replace(".", " ").toUpperCase()}
                </h4>
              </div>
              {metricStats.error_rate > 0.1 && (
                <XCircle className="w-4 h-4 text-red-400" />
              )}
            </div>

            {/* 统计数据 */}
            <div className="space-y-2 text-xs">
              <div className="flex justify-between">
                <span className="text-gray-400">调用次数:</span>
                <span className="text-white">{metricStats.count}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-400">平均延迟:</span>
                <span className="text-purple-300">{formatTime(metricStats.avg_time)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-400">最小延迟:</span>
                <span className="text-green-300">{formatTime(metricStats.min_time)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-400">最大延迟:</span>
                <span className="text-yellow-300">{formatTime(metricStats.max_time)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-gray-400">错误率:</span>
                <span
                  className={cn(
                    metricStats.error_rate > 0.1 ? "text-red-400" : "text-green-400"
                  )}
                >
                  {(metricStats.error_rate * 100).toFixed(1)}%
                </span>
              </div>
              {metricStats.errors > 0 && (
                <div className="flex justify-between">
                  <span className="text-gray-400">错误次数:</span>
                  <span className="text-red-400">{metricStats.errors}</span>
                </div>
              )}
            </div>

            {/* 性能条 */}
            <div className="mt-3 pt-3 border-t border-gray-700">
              <div className="flex items-center gap-2">
                <TrendingUp className="w-3 h-3 text-gray-400" />
                <div className="flex-1 h-2 bg-gray-700 rounded-full overflow-hidden">
                  <div
                    className={cn(
                      "h-full transition-all",
                      metricStats.avg_time > 2.0
                        ? "bg-red-500"
                        : metricStats.avg_time > 1.0
                        ? "bg-yellow-500"
                        : "bg-green-500"
                    )}
                    style={{
                      width: `${Math.min(100, (metricStats.avg_time / 5.0) * 100)}%`,
                    }}
                  />
                </div>
              </div>
            </div>
          </motion.div>
        ))}
      </div>

      {/* 如果没有数据 */}
      {Object.keys(stats).length === 0 && (
        <div className="text-center text-gray-400 py-8">
          <Clock className="w-12 h-12 mx-auto mb-2 opacity-50" />
          <p>暂无性能数据</p>
          <p className="text-xs mt-2">执行一些操作后，性能数据将显示在这里</p>
        </div>
      )}
    </div>
  );
};
