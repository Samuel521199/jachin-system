/**
 * SDRendererProgressBar - 进度条组件渲染器
 */

import React from "react";
import { motion } from "framer-motion";
import { cn } from "../../utils/cn";

interface SDRendererProgressBarProps {
  title?: string;
  value: number;
  max_value?: number;
  show_percentage?: boolean;
  status_text?: string;
  color?: string;
  className?: string;
}

export const SDRendererProgressBar: React.FC<SDRendererProgressBarProps> = ({
  title,
  value,
  max_value = 100,
  show_percentage = true,
  status_text,
  color = "purple",
  className,
}) => {
  const percentage = Math.min(100, Math.max(0, (value / max_value) * 100));

  const colorClasses = {
    purple: "bg-purple-600",
    blue: "bg-blue-600",
    green: "bg-green-600",
    yellow: "bg-yellow-600",
    red: "bg-red-600",
  };

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      className={cn("sdui-progressbar", className)}
    >
      {title && (
        <div className="flex justify-between items-center mb-2">
          <span className="text-sm text-purple-300">{title}</span>
          {show_percentage && (
            <span className="text-sm text-gray-400">{percentage.toFixed(1)}%</span>
          )}
        </div>
      )}
      <div className="w-full bg-gray-700 rounded-full h-2 overflow-hidden">
        <motion.div
          initial={{ width: 0 }}
          animate={{ width: `${percentage}%` }}
          transition={{ duration: 0.5, ease: "easeOut" }}
          className={cn("h-full rounded-full", colorClasses[color as keyof typeof colorClasses] || colorClasses.purple)}
        />
      </div>
      {status_text && (
        <div className="text-xs text-gray-400 mt-1">{status_text}</div>
      )}
    </motion.div>
  );
};
