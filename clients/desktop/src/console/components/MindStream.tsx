/**
 * MindStream - 实时思维流
 * 深空悬浮字排 + 打字机行；统计/日志行淡入
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
  onDone: () => void;
}

const StreamLine = forwardRef<HTMLDivElement, StreamLineProps>(function StreamLine({ line, onDone }, ref) {
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
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, ease: "easeOut" }}
      className={cn(
        "font-mono text-sm leading-relaxed",
        "text-slate-300 [text-shadow:0_0_20px_rgba(6,182,212,0.12),0_0_40px_rgba(0,0,0,0.9)]"
      )}
      style={{ fontFamily: "'JetBrains Mono', 'Fira Code', monospace" }}
    >
      <span className="select-none text-cyan-500/70">›</span>{" "}
      <span>{display}</span>
      {!done && <span className="ml-0.5 inline-block h-4 w-0.5 animate-pulse bg-cyan-400/90 shadow-[0_0_8px_rgba(34,211,238,0.8)]" />}
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
        "relative flex h-full min-h-0 flex-col overflow-hidden",
        "before:pointer-events-none before:absolute before:left-0 before:top-0 before:h-3 before:w-3 before:border-l before:border-t before:border-cyan-500/35",
        "after:pointer-events-none after:absolute after:right-0 after:top-0 after:h-3 after:w-3 after:border-r after:border-t after:border-cyan-500/35",
        className
      )}
    >
      <div className="pointer-events-none absolute bottom-0 left-0 h-3 w-3 border-b border-l border-cyan-500/25" />
      <div className="pointer-events-none absolute bottom-0 right-0 h-3 w-3 border-b border-r border-cyan-500/25" />

      <div className="flex flex-shrink-0 items-center gap-2 border-b border-cyan-500/20 px-1 py-2">
        <span
          className={cn(
            "h-1.5 w-1.5 animate-pulse rounded-none",
            displayLines.some(lineLooksDisconnected) || liveStatsLines.some(statsLookDisconnected)
              ? "bg-amber-500 shadow-[0_0_10px_rgba(245,158,11,0.8)]"
              : "bg-emerald-500 shadow-[0_0_10px_rgba(52,211,153,0.55)]"
          )}
          title={
            displayLines.some(lineLooksDisconnected) || liveStatsLines.some(statsLookDisconnected)
              ? (mindLocale?.statusError ?? "连接异常")
              : (mindLocale?.statusLive ?? "Live")
          }
        />
        <span
          className="text-[10px] font-semibold uppercase tracking-[0.25em] text-cyan-600/90"
          style={{ fontFamily: "Orbitron, sans-serif" }}
        >
          Mind Stream
        </span>
        <span className="ml-auto font-mono text-[10px] uppercase tracking-widest text-slate-600">
          Tier 2 · Live
        </span>
      </div>
      <div
        ref={scrollRef}
        className="custom-scrollbar min-h-0 flex-1 space-y-2 overflow-y-auto px-1 py-3"
        style={{
          background:
            "radial-gradient(ellipse 80% 60% at 50% 0%, rgba(6,182,212,0.06) 0%, transparent 55%), rgba(0,0,0,0.25)",
        }}
      >
        <AnimatePresence mode="popLayout">
          {displayLines.map((line, i) => (
            <StreamLine key={`${i}-${line.slice(0, 12)}`} line={line} onDone={() => {}} />
          ))}
        </AnimatePresence>
        {liveStatsLines.length > 0 && (
          <div className="space-y-2 border-t border-cyan-500/15 pt-3">
            {liveStatsLines.map((liveLine, i) => (
              <motion.div
                key={`live-${i}-${liveLine.slice(0, 20)}`}
                initial={{ opacity: 0, x: -6 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ duration: 0.4, delay: i * 0.05 }}
                className="font-mono text-sm text-cyan-400/95 [text-shadow:0_0_14px_rgba(34,211,238,0.25)]"
                style={{ fontFamily: "'JetBrains Mono', 'Fira Code', monospace" }}
              >
                <span className="select-none text-cyan-500/70">◆</span> {liveLine}
              </motion.div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
