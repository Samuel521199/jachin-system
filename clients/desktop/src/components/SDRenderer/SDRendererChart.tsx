/**
 * SDRendererChart - 图表组件渲染器
 * 
 * 使用 Recharts 渲染各种类型的图表
 */

import React from "react";
import {
  LineChart,
  Line,
  BarChart,
  Bar,
  PieChart,
  Pie,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  Cell,
} from "recharts";
import { motion } from "framer-motion";
import { cn } from "../../utils/cn";

interface SDRendererChartProps {
  chart_type: "line" | "bar" | "pie" | "area";
  title?: string;
  data: Array<Record<string, any>>;
  x_axis_label?: string;
  y_axis_label?: string;
  show_legend?: boolean;
  height?: string;
  className?: string;
}

const COLORS = ["#8b5cf6", "#ec4899", "#06b6d4", "#10b981", "#f59e0b"];

export const SDRendererChart: React.FC<SDRendererChartProps> = ({
  chart_type,
  title,
  data,
  x_axis_label,
  y_axis_label,
  show_legend = true,
  height = "200px",
  className,
}) => {
  // 提取数据键（排除第一个键，通常是 X 轴）
  const dataKeys = data.length > 0 ? Object.keys(data[0]) : [];
  const xKey = dataKeys[0] || "name";
  const yKeys = dataKeys.slice(1);

  const chartHeight = parseInt(height) || 200;

  const renderChart = () => {
    switch (chart_type) {
      case "line":
        return (
          <LineChart data={data}>
            <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
            <XAxis
              dataKey={xKey}
              stroke="#9ca3af"
              label={x_axis_label ? { value: x_axis_label, position: "insideBottom", offset: -5 } : undefined}
            />
            <YAxis
              stroke="#9ca3af"
              label={y_axis_label ? { value: y_axis_label, angle: -90, position: "insideLeft" } : undefined}
            />
            <Tooltip
              contentStyle={{
                backgroundColor: "rgba(17, 24, 39, 0.9)",
                border: "1px solid rgba(139, 92, 246, 0.3)",
                borderRadius: "8px",
              }}
            />
            {show_legend && <Legend />}
            {yKeys.map((key, index) => (
              <Line
                key={key}
                type="monotone"
                dataKey={key}
                stroke={COLORS[index % COLORS.length]}
                strokeWidth={2}
                dot={{ fill: COLORS[index % COLORS.length], r: 4 }}
              />
            ))}
          </LineChart>
        );

      case "bar":
        return (
          <BarChart data={data}>
            <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
            <XAxis
              dataKey={xKey}
              stroke="#9ca3af"
              label={x_axis_label ? { value: x_axis_label, position: "insideBottom", offset: -5 } : undefined}
            />
            <YAxis
              stroke="#9ca3af"
              label={y_axis_label ? { value: y_axis_label, angle: -90, position: "insideLeft" } : undefined}
            />
            <Tooltip
              contentStyle={{
                backgroundColor: "rgba(17, 24, 39, 0.9)",
                border: "1px solid rgba(139, 92, 246, 0.3)",
                borderRadius: "8px",
              }}
            />
            {show_legend && <Legend />}
            {yKeys.map((key, index) => (
              <Bar
                key={key}
                dataKey={key}
                fill={COLORS[index % COLORS.length]}
                radius={[4, 4, 0, 0]}
              />
            ))}
          </BarChart>
        );

      case "pie":
        // Pie chart 需要特殊处理数据格式
        const pieData = yKeys.length > 0
          ? data.map((item) => ({
              name: item[xKey],
              value: item[yKeys[0]],
            }))
          : [];

        return (
          <PieChart>
            <Pie
              data={pieData}
              cx="50%"
              cy="50%"
              labelLine={false}
              label={({ name, percent }) => `${name}: ${((percent ?? 0) * 100).toFixed(0)}%`}
              outerRadius={80}
              fill="#8884d8"
              dataKey="value"
            >
              {pieData.map((entry, index) => (
                <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
              ))}
            </Pie>
            <Tooltip
              contentStyle={{
                backgroundColor: "rgba(17, 24, 39, 0.9)",
                border: "1px solid rgba(139, 92, 246, 0.3)",
                borderRadius: "8px",
              }}
            />
            {show_legend && <Legend />}
          </PieChart>
        );

      case "area":
        return (
          <AreaChart data={data}>
            <defs>
              {yKeys.map((key, index) => (
                <linearGradient key={key} id={`color${index}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor={COLORS[index % COLORS.length]} stopOpacity={0.8} />
                  <stop offset="95%" stopColor={COLORS[index % COLORS.length]} stopOpacity={0} />
                </linearGradient>
              ))}
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
            <XAxis
              dataKey={xKey}
              stroke="#9ca3af"
              label={x_axis_label ? { value: x_axis_label, position: "insideBottom", offset: -5 } : undefined}
            />
            <YAxis
              stroke="#9ca3af"
              label={y_axis_label ? { value: y_axis_label, angle: -90, position: "insideLeft" } : undefined}
            />
            <Tooltip
              contentStyle={{
                backgroundColor: "rgba(17, 24, 39, 0.9)",
                border: "1px solid rgba(139, 92, 246, 0.3)",
                borderRadius: "8px",
              }}
            />
            {show_legend && <Legend />}
            {yKeys.map((key, index) => (
              <Area
                key={key}
                type="monotone"
                dataKey={key}
                stroke={COLORS[index % COLORS.length]}
                fill={`url(#color${index})`}
                strokeWidth={2}
              />
            ))}
          </AreaChart>
        );

      default:
        return <div className="text-yellow-400">Unsupported chart type: {chart_type}</div>;
    }
  };

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 0.3 }}
      className={cn("sdui-chart", className)}
    >
      {title && (
        <h3 className="text-sm font-semibold text-purple-300 mb-2">{title}</h3>
      )}
      <div style={{ height: chartHeight, width: "100%" }}>
        <ResponsiveContainer width="100%" height="100%">
          {renderChart()}
        </ResponsiveContainer>
      </div>
    </motion.div>
  );
};
