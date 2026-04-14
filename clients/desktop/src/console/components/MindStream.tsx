/**
 * MindStream - 实时思维流
 * 滚动日志窗口，打字机效果显示 Tier 2 的实时操作（模拟 / 后续接真实日志）
 */

import { useState, useEffect, useRef, useCallback, forwardRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { cn } from "../../utils/cn";

const DEMO_LINES: string[] = [
  "Analyzing user intent...",
  "Optimizing memory index...",
  "Ray cluster: 2 nodes active (1 GPU).",
  "LanceDB: 1,247 vectors indexed.",
  "Skill [files.search] invoked.",
  "Context window: 4,096 / 8,192 tokens.",
  "Eagle eye: console focus requested.",
  "Backend latency: 12ms.",
  "Indexing 'project_docs.pdf' in background...",
  "CPU temperature nominal. No throttling.",
  "Device registry: 3 devices online.",
];

function useTypewriter(line: string, speed = 28, enabled = true) {
  const [display, setDisplay] = useState("");
  const [done, setDone] = useState(false);

  useEffect(() => {
    if (!enabled || !line) {
      setDisplay(line);
      setDone(true);
      return;
    }
    setDisplay("");
    setDone(false);
    let i = 0;
    const t = setInterval(() => {
      i += 1;
      setDisplay(line.slice(0, i));
      if (i >= line.length) {
        clearInterval(t);
        setDone(true);
      }
    }, speed);
    return () => clearInterval(t);
  }, [line, speed, enabled]);

  return { display, done };
}

interface StreamLineProps {
  line: string;
  index: number;
  onDone: () => void;
}

const StreamLine = forwardRef<HTMLDivElement, StreamLineProps>(function StreamLine({ line, index, onDone }, ref) {
  const { display, done } = useTypewriter(line, 22, true);
  const wasDone = useRef(false);
  useEffect(() => {
    if (done && !wasDone.current) {
      wasDone.current = true;
      onDone();
    }
  }, [done, onDone]);

  return (
    <motion.div
      ref={ref}
      layout
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      className={cn(
        "font-mono text-sm leading-relaxed",
        "text-slate-400"
      )}
      style={{ fontFamily: "'JetBrains Mono', 'Fira Code', monospace" }}
    >
      <span className="text-rose-500/80 select-none">›</span>{" "}
      <span className="text-slate-300">{display}</span>
      {!done && (
        <span className="inline-block w-2 h-4 ml-0.5 bg-cyan-400/90 animate-pulse" />
      )}
    </motion.div>
  );
});

export type MindStreamLocale = {
  waiting1: string;
  waiting2: string;
  statusLive: string;
  statusError: string;
};

export function MindStream({
  className,
  maxLines = 8,
  demoLoop = true,
  /** 底部实时统计行（由父组件轮询 API 注入，不参与打字机） */
  liveStatsLines = [],
  /** 从后端 /api/v3/logs/recent 获取的日志行，有则优先显示，无则用 demo */
  liveLogLines = [],
  /** 无后端日志时的占位与状态角标文案 */
  mindLocale,
  /** 英文界面下将常见中文调试句替换为可读英文（专名不改） */
  localizeLine,
}: {
  className?: string;
  maxLines?: number;
  demoLoop?: boolean;
  liveStatsLines?: string[];
  liveLogLines?: string[];
  mindLocale?: MindStreamLocale;
  localizeLine?: (line: string) => string;
}) {
  const [lines, setLines] = useState<string[]>([]);
  const nextIndexRef = useRef(0);
  const scrollRef = useRef<HTMLDivElement>(null);

  const addLine = useCallback(() => {
    const line = DEMO_LINES[nextIndexRef.current % DEMO_LINES.length];
    nextIndexRef.current += 1;
    setLines((prev) => [...prev.slice(-(maxLines - 1)), line]);
  }, [maxLines]);

  useEffect(() => {
    addLine();
  }, [addLine]);

  useEffect(() => {
    if (!demoLoop || liveLogLines.length > 0) return;
    const t = setInterval(addLine, 3200);
    return () => clearInterval(t);
  }, [demoLoop, addLine, liveLogLines.length]);

  const fallbackWait = mindLocale
    ? [mindLocale.waiting1, mindLocale.waiting2]
    : ["等待连接…", "请确保后端已启动 (scripts\\start.ps1)"];

  const rawDisplayLines = liveLogLines.length > 0
    ? liveLogLines.slice(-maxLines)
    : lines.length > 0
      ? lines
      : fallbackWait;

  const displayLines = localizeLine
    ? rawDisplayLines.map((line) => localizeLine(line))
    : rawDisplayLines;

  const lineLooksDisconnected = (l: string) =>
    /连接失败|未连接|disconnected|connection failed|not connected/i.test(l);
  const statsLookDisconnected = (l: string) =>
    /未连接|not connected|disconnected/i.test(l);

  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [displayLines, liveStatsLines]);

  return (
    <div
      className={cn(
        "glass-panel rounded-xl overflow-hidden flex flex-col h-full min-h-0",
        className
      )}
    >
      <div className="flex-shrink-0 px-4 py-2 border-b border-white/10 flex items-center gap-2">
        <span
          className={cn(
            "w-2 h-2 rounded-full animate-pulse",
            displayLines.some(lineLooksDisconnected) ||
            liveStatsLines.some(statsLookDisconnected)
              ? "bg-amber-500"
              : "bg-emerald-500"
          )}
          title={
            displayLines.some(lineLooksDisconnected) ||
            liveStatsLines.some(statsLookDisconnected)
              ? (mindLocale?.statusError ?? "连接异常")
              : (mindLocale?.statusLive ?? "Live")
          }
        />
        <span
          className="text-xs font-semibold uppercase tracking-wider text-slate-400"
          style={{ fontFamily: "Orbitron, sans-serif" }}
        >
          Mind Stream
        </span>
        <span className="text-[10px] text-slate-500 font-mono ml-auto">
          Tier 2 · Live
        </span>
      </div>
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto p-4 space-y-2 custom-scrollbar min-h-0"
      >
        <AnimatePresence mode="popLayout">
          {displayLines.map((line, i) => (
            <StreamLine
              key={`${i}-${line.slice(0, 12)}`}
              line={line}
              index={i}
              onDone={() => {}}
            />
          ))}
        </AnimatePresence>
        {liveStatsLines.length > 0 && (
          <div className="pt-2 mt-2 border-t border-white/10 space-y-1">
            {liveStatsLines.map((liveLine, i) => (
              <div
                key={`live-${i}-${liveLine.slice(0, 20)}`}
                className="font-mono text-sm text-cyan-400/90"
                style={{ fontFamily: "'JetBrains Mono', 'Fira Code', monospace" }}
              >
                <span className="text-cyan-500/80 select-none">◆</span>{" "}
                {liveLine}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
