/**
 * Omni 助手「思考链」— 默认折叠，与正文视觉分层（弱对比、等宽小字）
 */
import React from "react";
import { ChevronDown, Sparkles } from "lucide-react";

export interface OmniReasoningChainProps {
  text: string;
  /** 当前轮仍在流式写入 reasoning 时显示轻提示 */
  isStreaming?: boolean;
}

export const OmniReasoningChain: React.FC<OmniReasoningChainProps> = ({ text, isStreaming }) => {
  const trimmed = text.trim();
  if (!trimmed) return null;

  return (
    <details className="omni-reasoning-chain group mb-2.5 rounded-xl border-0 bg-slate-900/35 shadow-none outline-none ring-0 backdrop-blur-sm focus:outline-none focus-visible:outline-none">
      <summary className="flex cursor-pointer select-none list-none items-center gap-2 rounded-lg px-3 py-2 text-left outline-none ring-0 ring-offset-0 focus:outline-none focus-visible:outline-none [&::-webkit-details-marker]:hidden">
        <Sparkles className="h-3.5 w-3.5 shrink-0 text-cyan-500/50" aria-hidden />
        <span className="text-[12px] font-medium tracking-wide text-cyan-400/80">思考链</span>
        <span className="text-[10px] text-cyan-600/50">（可展开）</span>
        {isStreaming ? (
          <span className="rounded bg-cyan-500/10 px-1.5 py-0.5 text-[9px] font-mono text-cyan-500/55">
            更新中
          </span>
        ) : null}
        <ChevronDown
          className="ml-auto h-4 w-4 shrink-0 text-cyan-500/45 transition-transform duration-200 group-open:rotate-180"
          aria-hidden
        />
      </summary>
      <div className="max-h-[min(38vh,300px)] overflow-y-auto border-x-0 border-b-0 border-t border-cyan-500/10 px-3 py-2.5 pt-2">
        <pre className="whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed text-cyan-200/45">
          {trimmed}
        </pre>
      </div>
    </details>
  );
};

export default OmniReasoningChain;
