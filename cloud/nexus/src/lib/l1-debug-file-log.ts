/**
 * L1 Nexus 文件调试日志（默认 ~/.jachin/l1_debug.log，Windows 即 C:\Users\<用户>\.jachin\l1_debug.log）。
 * 失败时静默，不影响 API。可通过 JACHIN_L1_DEBUG_LOG 覆盖路径；JACHIN_L1_DEBUG=0 可关闭写入。
 */
import { appendFileSync, mkdirSync } from "fs";
import { dirname } from "path";
import os from "os";
import path from "path";

export function getL1DebugLogPath(): string {
  const override = (process.env.JACHIN_L1_DEBUG_LOG ?? "").trim();
  if (override) return override;
  return path.join(os.homedir(), ".jachin", "l1_debug.log");
}

export function isL1FileDebugEnabled(): boolean {
  return (process.env.JACHIN_L1_DEBUG ?? "1").trim() !== "0";
}

/**
 * 追加一行 NDJSON（UTF-8）。含时间戳、进程、事件名与负载。
 */
export function appendL1DebugLine(
  event: string,
  payload: Record<string, unknown> & { msg?: string }
): void {
  if (!isL1FileDebugEnabled()) return;
  try {
    const p = getL1DebugLogPath();
    mkdirSync(dirname(p), { recursive: true });
    const line =
      JSON.stringify({
        ts: new Date().toISOString(),
        pid: process.pid,
        node: process.version,
        cwd: process.cwd(),
        event,
        ...payload,
      }) + "\n";
    appendFileSync(p, line, { encoding: "utf8" });
  } catch {
    /* 绝不影响主流程 */
  }
}

export function appendL1DebugError(event: string, err: unknown, extra?: Record<string, unknown>): void {
  const message = err instanceof Error ? err.message : String(err);
  const stack = err instanceof Error ? err.stack : undefined;
  appendL1DebugLine(event, { level: "error", msg: message, stack, ...extra });
}
