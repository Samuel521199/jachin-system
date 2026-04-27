/**
 * L3 控制台消息中心 — 与右下角 Jachin 哨兵 toast 同源（Rust `~/.jachin/console_inbox.json`）
 */
import { invoke } from "@tauri-apps/api/core";

export type ConsoleInboxItem = {
  id: string;
  title: string;
  body: string;
  created_at_ms: number;
  read: boolean;
};

export async function fetchConsoleInbox(): Promise<ConsoleInboxItem[]> {
  try {
    return await invoke<ConsoleInboxItem[]>("jachin_inbox_list");
  } catch {
    return [];
  }
}

export async function markConsoleInboxRead(id: string): Promise<boolean> {
  try {
    await invoke("jachin_inbox_mark_read", { id });
    return true;
  } catch {
    return false;
  }
}

export async function markAllConsoleInboxRead(): Promise<boolean> {
  try {
    await invoke("jachin_inbox_mark_all_read");
    return true;
  } catch {
    return false;
  }
}
