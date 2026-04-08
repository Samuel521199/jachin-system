/**
 * 热更新调试：追加写入 D:\\zzz\\jachin\\hot_update_debug.log（Rust 侧实现，不落盘 token）。
 */
import { invoke } from "@tauri-apps/api/core";

export async function updaterDebugLog(line: string): Promise<void> {
  try {
    await invoke("updater_debug_append", { line });
  } catch (e) {
    console.warn("[updaterDebugLog] invoke failed:", e);
  }
}
