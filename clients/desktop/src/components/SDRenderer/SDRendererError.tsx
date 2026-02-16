/**
 * SDRendererError - 错误卡片组件
 * 
 * 用于显示插件调用失败、LLM 错误等错误信息
 */

import React from "react";
import { motion } from "framer-motion";
import { AlertCircle, RefreshCw, X } from "lucide-react";
import { cn } from "../../utils/cn";

interface SDRendererErrorProps {
  title?: string;
  message: string;
  code?: string;
  traceId?: string;
  retryable?: boolean;
  onRetry?: () => void;
  onClose?: () => void;
  className?: string;
}

export const SDRendererError: React.FC<SDRendererErrorProps> = ({
  title = "操作失败",
  message,
  code,
  traceId,
  retryable = false,
  onRetry,
  onClose,
  className,
}) => {
  return (
    <motion.div
      initial={{ opacity: 0, y: -10 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -10 }}
      className={cn(
        "sdui-error-card",
        "bg-red-900/20 border border-red-500/30 rounded-lg p-4",
        "backdrop-blur-sm",
        className
      )}
    >
      {/* 标题栏 */}
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center gap-2">
          <AlertCircle className="w-5 h-5 text-red-400 flex-shrink-0" />
          <h3 className="text-lg font-semibold text-red-300">{title}</h3>
        </div>
        {onClose && (
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-white transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        )}
      </div>

      {/* 错误消息 */}
      <div className="mb-3">
        <p className="text-white text-sm leading-relaxed">{message}</p>
      </div>

      {/* 错误详情 */}
      {(code || traceId) && (
        <div className="mb-3 space-y-1 text-xs text-gray-400">
          {code && (
            <div>
              <span className="text-gray-500">错误码:</span>{" "}
              <code className="text-red-400 font-mono">{code}</code>
            </div>
          )}
          {traceId && (
            <div>
              <span className="text-gray-500">追踪ID:</span>{" "}
              <code className="text-gray-400 font-mono">{traceId}</code>
            </div>
          )}
        </div>
      )}

      {/* 操作按钮 */}
      {retryable && onRetry && (
        <motion.button
          whileHover={{ scale: 1.05 }}
          whileTap={{ scale: 0.95 }}
          onClick={onRetry}
          className={cn(
            "flex items-center gap-2 px-4 py-2 rounded-lg",
            "bg-red-600 hover:bg-red-700 text-white",
            "transition-colors text-sm font-medium"
          )}
        >
          <RefreshCw className="w-4 h-4" />
          重试
        </motion.button>
      )}
    </motion.div>
  );
};
