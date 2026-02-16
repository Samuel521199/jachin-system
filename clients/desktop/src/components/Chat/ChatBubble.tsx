/**
 * ChatBubble - Cyber-Heart 风格的消息气泡组件
 * 
 * AI (左侧): 玻璃拟态 + 红色描边 + 爱心装饰
 * User (右侧): 半透明背景 + 银色描边
 */

import React from "react";
import { motion } from "framer-motion";
import { Heart } from "lucide-react";
import { cn } from "../../utils/cn";

interface ChatBubbleProps {
  role: "user" | "assistant";
  content: string;
  timestamp?: number;
  isTyping?: boolean;
}

export const ChatBubble: React.FC<ChatBubbleProps> = ({
  role,
  content,
  timestamp,
  isTyping = false,
}) => {
  const isAI = role === "assistant";

  return (
    <motion.div
      className={cn(
        "flex items-start gap-3",
        isAI ? "justify-start" : "justify-end"
      )}
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ type: "spring", stiffness: 300, damping: 30 }}
    >
      {isAI && (
        <div className="flex-shrink-0 w-8 h-8 rounded-full bg-gradient-to-br from-rose-500/20 to-red-600/20 border border-rose-500/50 flex items-center justify-center">
          <Heart className="w-4 h-4 text-rose-400 fill-rose-400" />
        </div>
      )}

      <div
        className={cn(
          "max-w-[75%] px-4 py-3 backdrop-blur-md",
          isAI
            ? "bg-white/10 border border-rose-500/50 rounded-r-2xl rounded-tl-2xl shadow-[0_0_10px_rgba(244,63,94,0.2)]"
            : "bg-black/20 border border-slate-400/30 rounded-l-2xl rounded-tr-2xl shadow-[0_0_10px_rgba(148,163,184,0.1)]"
        )}
      >
        <p
          className={cn(
            "text-sm leading-relaxed",
            isAI ? "text-white" : "text-slate-200"
          )}
        >
          {isTyping ? (
            <span className="inline-flex items-center gap-1">
              {content}
              <span className="inline-block w-2 h-2 bg-rose-400 rounded-full animate-pulse" />
            </span>
          ) : (
            content
          )}
        </p>
        {timestamp && (
          <span
            className={cn(
              "text-xs mt-1 block",
              isAI ? "text-rose-300/60" : "text-slate-400/60"
            )}
          >
            {new Date(timestamp).toLocaleTimeString("zh-CN", {
              hour: "2-digit",
              minute: "2-digit",
            })}
          </span>
        )}
      </div>

      {!isAI && (
        <div className="flex-shrink-0 w-8 h-8 rounded-full bg-gradient-to-br from-slate-600/20 to-slate-700/20 border border-slate-400/30 flex items-center justify-center">
          <span className="text-xs text-slate-300">U</span>
        </div>
      )}
    </motion.div>
  );
};
