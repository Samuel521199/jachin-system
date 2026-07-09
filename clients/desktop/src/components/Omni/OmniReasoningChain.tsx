/**
 * Omni 助手「思考链」— 固定 max-h-48、内部滚动、半透明弱对比；与主文严格分层
 */
import React, { useEffect, useRef, useState } from "react";
import { ChevronDown, Sparkles } from "lucide-react";

export interface OmniReasoningChainLabels {
  chain: string;
  expand: string;
  updating: string;
}

export interface OmniReasoningChainProps {
  text: string;
  isStreaming?: boolean;
  labels?: OmniReasoningChainLabels;
}

const DEFAULT_LABELS: OmniReasoningChainLabels = {
  chain: "思考链",
  expand: "（可展开）",
  updating: "更新中",
};

export const OmniReasoningChain: React.FC<OmniReasoningChainProps> = ({
  text,
  isStreaming,
  labels = DEFAULT_LABELS,
}) => {
  const trimmed = text.trim();
  const [open, setOpen] = useState(() => Boolean(isStreaming));
  const reasoningEndRef = useRef<HTMLDivElement>(null);
  const prevStreamingRef = useRef(false);

  useEffect(() => {
    if (isStreaming) setOpen(true);
  }, [isStreaming]);

  /** 流式结束：自动收起思考链，正文成为视觉焦点 */
  useEffect(() => {
    const streaming = Boolean(isStreaming);
    if (prevStreamingRef.current && !streaming) {
      setOpen(false);
    }
    prevStreamingRef.current = streaming;
  }, [isStreaming]);

  /** 流式追加思考文字时，保持滚动区锚定在底部，最新输出始终可见 */
  useEffect(() => {
    if (!trimmed) return;
    const id = requestAnimationFrame(() => {
      reasoningEndRef.current?.scrollIntoView({
        behavior: isStreaming ? "auto" : "smooth",
        block: "nearest",
        inline: "nearest",
      });
    });
    return () => cancelAnimationFrame(id);
  }, [text, trimmed, isStreaming, open]);

  if (!trimmed) return null;

  return (
    <details
      open={open}
      onToggle={(e) => {
        e.stopPropagation();
        setOpen((e.target as HTMLDetailsElement).open);
      }}
      className="omni-reasoning-chain group mb-2.5 w-full min-w-0 max-w-full rounded-lg border border-sky-300/10 bg-sky-300/[0.035] outline-none ring-0 focus:outline-none focus-visible:outline-none"
    >
      <summary className="flex min-w-0 cursor-pointer select-none list-none items-center gap-2 rounded-t-lg border-b border-sky-300/[0.045] bg-white/[0.025] px-2 py-2 text-left outline-none sm:px-3 [&::-webkit-details-marker]:hidden">
        <Sparkles className="h-3.5 w-3.5 shrink-0 text-sky-300/[0.48]" aria-hidden />
        <span className="min-w-0 shrink text-[12px] font-medium tracking-normal text-sky-100/[0.76]">{labels.chain}</span>
        <span className="hidden shrink-0 text-[10px] text-sky-200/[0.34] sm:inline">{labels.expand}</span>
        {isStreaming ? (
          <span className="rounded bg-sky-300/[0.08] px-1.5 py-0.5 text-[9px] text-sky-200/[0.58]">
            {labels.updating}
          </span>
        ) : null}
        <ChevronDown
          className="ml-auto h-4 w-4 shrink-0 text-sky-200/[0.38] transition-transform duration-200 group-open:rotate-180"
          aria-hidden
        />
      </summary>
      <div className="max-h-48 overflow-y-auto no-scrollbar border-t border-sky-300/[0.045] bg-black/[0.18] p-3 font-mono text-xs leading-relaxed text-slate-300/[0.58] break-words whitespace-pre-wrap">
        {trimmed}
        <div ref={reasoningEndRef} className="h-px w-full shrink-0 scroll-mt-1" aria-hidden />
      </div>
    </details>
  );
};

export default OmniReasoningChain;
