/**
 * 使用 ~/.jachin/nexus_config.json 的 desktop_update_token（或 access_token）注入 updater 请求头。
 * 服务端需校验 edge_agents 或 DESKTOP_UPDATE_BEARER；发布若用 --unsigned 占位签名，Tauri 端签名校验会失败，横幅不会出现。
 */
import { getVersion } from "@tauri-apps/api/app";
import { useCallback, useEffect, useState } from "react";
import { check, type Update } from "@tauri-apps/plugin-updater";
import { updaterDebugLog } from "../lib/updaterDebugLog";

const CHECK_INTERVAL_MS = 4 * 60 * 60 * 1000;

function summarizeUpdate(u: Update | null): string {
  if (!u) return "null (无可用更新或与服务端版本相同)";
  try {
    return JSON.stringify({
      currentVersion: u.currentVersion,
      version: u.version,
      date: u.date,
      body: u.body?.slice(0, 500),
      rawJson: u.rawJson,
    });
  } catch {
    return String(u);
  }
}

export function DesktopUpdateBanner() {
  const [pending, setPending] = useState<Update | null>(null);
  const [busy, setBusy] = useState(false);

  const runCheck = useCallback(async () => {
    const t0 = performance.now();
    let appVer = "";
    try {
      appVer = await getVersion();
    } catch {
      appVer = "(getVersion failed)";
    }
    await updaterDebugLog(
      `check START mode=${import.meta.env.MODE} CHECK_INTERVAL_MS=${CHECK_INTERVAL_MS} appVersion=${appVer}`
    );
    try {
      const u = await check();
      const ms = Math.round(performance.now() - t0);
      setPending(u);
      await updaterDebugLog(`check OK in ${ms}ms -> ${summarizeUpdate(u)}`);
    } catch (e) {
      const ms = Math.round(performance.now() - t0);
      const err =
        e instanceof Error
          ? `${e.name}: ${e.message}\n${e.stack ?? ""}`
          : JSON.stringify(e);
      console.warn("[DesktopUpdateBanner] check failed:", e);
      await updaterDebugLog(`check FAILED after ${ms}ms: ${err}`);
      if (import.meta.env.DEV) {
        console.info(
          "[DesktopUpdateBanner] 常见原因：① Nexus 未起 ② nexus_config 无 desktop_update_token ③ 发布为 unsigned 占位签名无法通过 Tauri 校验"
        );
      }
      setPending(null);
    }
  }, []);

  useEffect(() => {
    void runCheck();
    const t = window.setInterval(() => void runCheck(), CHECK_INTERVAL_MS);
    return () => window.clearInterval(t);
  }, [runCheck]);

  const onInstall = async () => {
    if (!pending) return;
    setBusy(true);
    await updaterDebugLog(
      `downloadAndInstall START targetVersion=${pending.version} from=${pending.currentVersion}`
    );
    try {
      await pending.downloadAndInstall((ev) => {
        void updaterDebugLog(`downloadAndInstall event: ${JSON.stringify(ev)}`);
      });
      await updaterDebugLog("downloadAndInstall FINISHED OK (即将退出并安装)");
    } catch (e) {
      const err =
        e instanceof Error
          ? `${e.name}: ${e.message}\n${e.stack ?? ""}`
          : JSON.stringify(e);
      console.error("[DesktopUpdateBanner] install failed:", e);
      await updaterDebugLog(`downloadAndInstall FAILED: ${err}`);
    } finally {
      setBusy(false);
    }
  };

  if (!pending) return null;

  return (
    <div className="shrink-0 z-[100] border-b border-amber-500/30 bg-amber-950/90 px-4 py-2 text-sm text-amber-100 flex flex-wrap items-center justify-between gap-3">
      <span>
        发现新版本 <strong className="text-white">{pending.version}</strong>
        ，可立即下载安装。本地记忆与配置保存在用户目录，一般不会因升级丢失。
      </span>
      <button
        type="button"
        disabled={busy}
        onClick={() => void onInstall()}
        className="rounded-lg bg-amber-500/90 px-4 py-1.5 font-medium text-slate-950 hover:bg-amber-400 disabled:opacity-50"
      >
        {busy ? "安装中…" : "立即更新"}
      </button>
    </div>
  );
}
