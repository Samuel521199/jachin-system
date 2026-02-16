/**
 * SDUICard - SDUI 卡片容器组件
 * 
 * 在 InputBar 上方显示半透明卡片，包含 SDUI 内容
 */

import React from "react";
import { motion, AnimatePresence } from "framer-motion";
import { X } from "lucide-react";
import { SDRenderer, SDUIElement } from "./SDRenderer";
import { SDRendererError } from "./SDRendererError";
import { cn } from "../../utils/cn";

interface SDUICardProps {
  isOpen: boolean;
  sduiSchema?: string | null;
  error?: {
    message: string;
    code?: string;
    traceId?: string;
    retryable?: boolean;
  } | null;
  onClose?: () => void;
  onRetry?: () => void;
  onSubmit?: (data: any) => void; // 处理表单提交
  className?: string;
}

export const SDUICard: React.FC<SDUICardProps> = ({
  isOpen,
  sduiSchema,
  error,
  onClose,
  onRetry,
  onSubmit,
  className,
}) => {
  let parsedSchema: SDUIElement | null = null;

  if (sduiSchema) {
    try {
      parsedSchema = JSON.parse(sduiSchema);
    } catch (error) {
      console.error("Failed to parse SDUI schema:", error);
    }
  }

  return (
    <AnimatePresence>
      {isOpen && (parsedSchema || error) && (
        <motion.div
          initial={{ opacity: 0, y: 20, scale: 0.95 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={{ opacity: 0, y: 20, scale: 0.95 }}
          transition={{ type: "spring", stiffness: 300, damping: 30 }}
          className={cn(
            "absolute bottom-full left-1/2 transform -translate-x-1/2 mb-4",
            "w-96 max-w-[90vw]",
            "bg-black/80 backdrop-blur-xl",
            "border border-purple-500/30 rounded-2xl",
            "shadow-[0_0_30px_rgba(139,92,246,0.3)]",
            "p-4",
            "z-50",
            className
          )}
          style={{ userSelect: "none" }}
        >
          {/* 关闭按钮 */}
          {onClose && (
            <button
              onClick={onClose}
              className="absolute top-2 right-2 w-6 h-6 flex items-center justify-center rounded-full bg-gray-700/50 hover:bg-gray-600/50 text-gray-400 hover:text-white transition-colors"
            >
              <X className="w-4 h-4" />
            </button>
          )}

          {/* SDUI 内容或错误 */}
          <div className="max-h-96 overflow-y-auto custom-scrollbar">
            {error ? (
              <SDRendererError
                message={error.message}
                code={error.code}
                traceId={error.traceId}
                retryable={error.retryable}
                onRetry={onRetry}
              />
            ) : parsedSchema ? (
              <SDRenderer element={parsedSchema} onSubmit={onSubmit} />
            ) : null}
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
};
