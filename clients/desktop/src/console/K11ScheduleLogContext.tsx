/**
 * 订阅 L3 定时 K11 批跑 SSE，把行写入共享缓冲供 K11 页 MIND STREAM 展示，并在开始时刻发 Tauri 桌面通知。
 * 与手动「启动统合冒烟」的 EventSource 独立，挂在控制台整壳，不随子页卸载而丢失长连接。
 */
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import {
  isPermissionGranted,
  requestPermission,
  sendNotification,
} from "@tauri-apps/plugin-notification";
import { getK11ScheduledSmokeLogStreamUrlAsync } from "../lib/api";

const K11ScheduleLogLinesContext = createContext<string[]>([]);

const MAX_UI_LINES = 4000;

export function K11ScheduleLogProvider({ children }: { children: ReactNode }) {
  const [lines, setLines] = useState<string[]>([]);
  const esRef = useRef<EventSource | null>(null);
  const reconnectRef = useRef<ReturnType<typeof window.setTimeout> | null>(null);

  const append = useCallback((s: string) => {
    setLines((prev) => [...prev, s].slice(-MAX_UI_LINES));
  }, []);

  useEffect(() => {
    let cancelled = false;

    const open = async () => {
      if (reconnectRef.current) {
        window.clearTimeout(reconnectRef.current);
        reconnectRef.current = null;
      }
      try {
        const url = await getK11ScheduledSmokeLogStreamUrlAsync();
        if (cancelled) return;
        esRef.current?.close();
        const es = new EventSource(url);
        esRef.current = es;
        es.onmessage = (ev: MessageEvent<string>) => {
          try {
            const data = JSON.parse(ev.data) as Record<string, unknown>;
            if (data.type === "scheduled_start") {
              const runs = typeof data.runs === "number" ? data.runs : "?";
              const iv = typeof data.interval_sec === "number" ? data.interval_sec : "?";
              const script = typeof data.script === "string" ? data.script : "smoke script";
              const ts = typeof data.ts === "number" ? data.ts : 0;
              const oneShot = runs === 1;
              append(
                oneShot
                  ? `> [定时] 到点已触发，执行 1 轮统合脚本（${script}）`
                  : `> [定时] 已按计划开始：共 ${String(runs)} 轮，轮次间隔 ${String(iv)} 秒（${script}）`
              );
              const dedupeKey = `jachin_k11_sched_start_${ts.toFixed(0)}`;
              const isRecent = ts > 0 && Date.now() / 1000 - ts < 300;
              if (typeof sessionStorage !== "undefined" && isRecent && !sessionStorage.getItem(dedupeKey)) {
                sessionStorage.setItem(dedupeKey, "1");
                void (async () => {
                  try {
                    const ok = await isPermissionGranted();
                    if (!ok) {
                      const p = await requestPermission();
                      if (p !== "granted") return;
                    }
                    await sendNotification({
                      title: "K11 统合定时冒烟",
                      body: oneShot
                        ? `到点已触发 1 轮。可在本页 MIND STREAM 查看输出。`
                        : `已开始：共 ${String(runs)} 轮，间隔 ${String(iv)} 秒。可在本页 MIND STREAM 查看输出。`,
                    });
                  } catch {
                    /* 通知失败不阻断 */
                  }
                })();
              }
              return;
            }
            if (data.type === "scheduled_done") {
              const ok = data.ok === true;
              append(
                ok
                  ? "> [定时] 全部轮次已结束。"
                  : "> [定时] 已结束（存在失败轮次，见上方输出）。"
              );
              return;
            }
            if (data.type === "scheduled_progress") {
              const r = data.round;
              const t = data.total;
              const c = data.exit_code;
              if (typeof r === "number" && typeof t === "number") {
                append(`> [定时] 第 ${r}/${t} 轮子进程已退出，code=${String(c ?? "?")}`);
              }
              return;
            }
            if (data.type === "error" && typeof data.message === "string") {
              append(`> [定时][ERROR] ${data.message}`);
              return;
            }
            if (typeof data.line === "string" && data.line.length > 0) {
              append(`> ${data.line}`);
            }
          } catch {
            /* 忽略单条解析错误 */
          }
        };
        es.onerror = () => {
          try {
            es.close();
          } catch {
            /* */
          }
          if (cancelled) return;
          reconnectRef.current = window.setTimeout(() => {
            void open();
          }, 5000);
        };
      } catch {
        if (!cancelled) {
          reconnectRef.current = window.setTimeout(() => {
            void open();
          }, 8000);
        }
      }
    };

    void open();
    return () => {
      cancelled = true;
      if (reconnectRef.current) window.clearTimeout(reconnectRef.current);
      try {
        esRef.current?.close();
      } catch {
        /* */
      }
    };
  }, [append]);

  return (
    <K11ScheduleLogLinesContext.Provider value={lines}>{children}</K11ScheduleLogLinesContext.Provider>
  );
}

export function useK11ScheduleLogLines(): string[] {
  return useContext(K11ScheduleLogLinesContext);
}
