/**
 * useSkillSync - L3 冷启动技能同步
 *
 * 非阻塞：App 挂载时触发 perform_startup_sync，用户可先进行基础对话。
 * 同步中显示「正在同步企业资产...」，完成后派发 inventory-updated 事件。
 */

import { useEffect, useState, useCallback } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { BACKEND_URL } from "../lib/api";
import { INVENTORY_UPDATED_EVENT } from "./useUISyncEventSource";

export interface SyncProgress {
  phase: "syncing" | "complete";
  current?: number;
  total?: number;
  item_id?: string;
  name?: string;
  synced?: number;
  skipped?: number;
  failed?: number;
}

export function useSkillSync() {
  const [syncing, setSyncing] = useState(false);
  const [progress, setProgress] = useState<SyncProgress | null>(null);

  const showVocabIfAvailable = useCallback(() => {
    void invoke("show_english_vocab_window_if_available").catch((e) => {
      console.warn("[SkillSync] English vocab auto show skipped:", e);
    });
  }, []);

  const startSync = useCallback(() => {
    setSyncing(true);
    setProgress({ phase: "syncing", current: 0, total: 0 });
    invoke("perform_startup_sync", { baseUrl: BACKEND_URL }).catch((e) => {
      console.warn("[SkillSync] 同步失败:", e);
      setSyncing(false);
      setProgress(null);
    });
  }, []);

  useEffect(() => {
    const unlistenProgress = listen<SyncProgress>("inventory-sync-progress", (ev) => {
      setProgress(ev.payload);
      if (ev.payload?.phase === "complete") {
        setSyncing(false);
        setProgress(null);
        showVocabIfAvailable();
        window.dispatchEvent(new CustomEvent(INVENTORY_UPDATED_EVENT, { detail: { type: "SYNC_COMPLETE" } }));
      }
    });

    const unlistenComplete = listen("inventory-sync-complete", () => {
      setSyncing(false);
      setProgress(null);
      showVocabIfAvailable();
      window.dispatchEvent(new CustomEvent(INVENTORY_UPDATED_EVENT, { detail: { type: "SYNC_COMPLETE" } }));
    });

    startSync();

    return () => {
      unlistenProgress.then((fn) => fn());
      unlistenComplete.then((fn) => fn());
    };
  }, [startSync, showVocabIfAvailable]);

  return { syncing, progress, startSync };
}
