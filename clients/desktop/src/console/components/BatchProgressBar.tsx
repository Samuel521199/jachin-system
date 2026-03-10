/**
 * BatchProgressBar - HR 透析镜批量分析赛博朋克进度条
 *
 * 深色终端风，荧光绿/电光蓝发光效果，SSE 流式进度展示
 */

import { useEffect, useRef, useState } from "react";
import type { SkillStreamEvent } from "../../lib/api";
import { displayNameFromFilename } from "../../lib/api";

export interface BatchProgressBarProps {
  /** 是否可见 */
  visible: boolean;
  /** 技能 ID */
  skillId: string;
  /** 技能名称 */
  skillName: string;
  /** 关闭回调（error 非空表示流式执行出错） */
  onClose: (error?: string) => void;
  /** SSE 事件异步迭代器 */
  stream: AsyncGenerator<SkillStreamEvent>;
}

export function BatchProgressBar({
  visible,
  skillId,
  skillName,
  onClose,
  stream,
}: BatchProgressBarProps) {
  const [current, setCurrent] = useState(0);
  const [total, setTotal] = useState(0);
  const [logs, setLogs] = useState<string[]>([]);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const logEndRef = useRef<HTMLDivElement>(null);
  const consumedRef = useRef(false);

  useEffect(() => {
    if (!visible || !stream || consumedRef.current) return;
    consumedRef.current = true;
    const consume = async () => {
      try {
        for await (const ev of stream) {
          if (ev.status === "progress") {
            setCurrent((c) => ev.current ?? c);
            setTotal((t) => ev.total ?? t);
            const fn = ev.filename ?? "";
            const display = displayNameFromFilename(fn) || fn || "未知";
            setLogs((prev) => [...prev, `> 解析完毕: ${display} [已落盘]`]);
          } else if (ev.status === "done") {
            setDone(true);
            break;
          } else if (ev.status === "error") {
            setError(ev.error ?? "未知错误");
            break;
          }
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setDone(true);
      }
    };
    void consume();
  }, [visible, stream]);

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs]);

  if (!visible) return null;

  const pct = total > 0 ? Math.round((current / total) * 100) : 0;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm"
      onClick={(e) => e.target === e.currentTarget && done && onClose()}
    >
      <div
        className="w-full max-w-lg rounded-xl border border-cyan-500/40 bg-[#0a0e14] p-5 shadow-2xl"
        style={{
          boxShadow: "0 0 30px rgba(0, 212, 255, 0.15), inset 0 0 60px rgba(0, 212, 255, 0.03)",
        }}
      >
        <div className="font-mono text-xs uppercase tracking-wider text-cyan-400/90 mb-3">
          [SYS.LOG] 沙箱持续输出中... {current} / {total || "?"} 份
        </div>

        {/* 进度条 - 电光蓝发光 */}
        <div className="h-2 rounded-full bg-slate-800/80 overflow-hidden mb-4">
          <div
            className="h-full rounded-full transition-all duration-300"
            style={{
              width: `${pct}%`,
              background: "linear-gradient(90deg, #00d4ff 0%, #00ff88 100%)",
              boxShadow: "0 0 12px rgba(0, 212, 255, 0.6), 0 0 24px rgba(0, 255, 136, 0.3)",
            }}
          />
        </div>

        {/* 滚动日志 */}
        <div className="max-h-40 overflow-y-auto rounded-lg bg-black/60 border border-slate-700/60 p-3 font-mono text-xs text-slate-300 custom-scrollbar">
          {logs.length === 0 && !error && (
            <span className="text-slate-500">等待沙箱输出...</span>
          )}
          {logs.map((line, i) => (
            <div key={i} className="text-cyan-300/90 py-0.5">
              {line}
            </div>
          ))}
          {error && (
            <div className="text-rose-400 py-1">[ERROR] {error}</div>
          )}
          <div ref={logEndRef} />
        </div>

        {done && (
          <button
            type="button"
            onClick={() => onClose(error ?? undefined)}
            className="mt-4 w-full py-2 rounded-lg font-mono text-sm bg-cyan-600/80 hover:bg-cyan-500 text-white transition-colors"
          >
            关闭
          </button>
        )}
      </div>
    </div>
  );
}
