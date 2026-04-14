/**
 * 追加写入 ~/.jachin/l3_debug.log（与 L3 Python 共用），便于排查 Omni 陪伴态 / 哨兵弹窗。
 */
import { invoke } from "@tauri-apps/api/core";

export async function desktopDiagLog(category: string, payload: Record<string, unknown>): Promise<void> {
  try {
    const message = JSON.stringify({
      ts: Date.now(),
      ...payload,
    });
    await invoke("desktop_diag_log", { category, message });
  } catch {
    /* 浏览器预览或非 Tauri */
  }
}
