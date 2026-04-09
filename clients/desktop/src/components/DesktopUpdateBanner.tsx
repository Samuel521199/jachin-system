/**
 * 热更新：check 仍走 Tauri updater（带 Nexus Bearer）；「立即更新」先由助手在后台下载并校验，
 * 主程序保持运行；就绪后弹窗由用户选择是否立即重启完成替换（确认后主进程才退出）。
 */
import { invoke } from "@tauri-apps/api/core";
import { getVersion } from "@tauri-apps/api/app";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import { useCallback, useEffect, useRef, useState } from "react";
import { check, type Update } from "@tauri-apps/plugin-updater";
import { updaterDebugLog } from "../lib/updaterDebugLog";

type HotUpdatePrepareResult = {
  ok: boolean;
  stagedNewExe?: string | null;
  newVersion: string;
  error?: string | null;
};

/** 定时轮询：开发环境缩短便于刚发布后立即看到横幅；生产默认 1h */
const CHECK_INTERVAL_MS = import.meta.env.DEV
  ? 5 * 60 * 1000
  : 60 * 60 * 1000;

const VISIBILITY_RECHECK_THROTTLE_MS = 2 * 60 * 1000;

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

/** 从 Nexus/Tauri 静态 JSON 中选当前平台产物（优先 windows-*）。 */
function pickPlatformArtifact(raw: unknown): {
  url: string;
  signature: string;
} | null {
  if (!raw || typeof raw !== "object") return null;
  const platforms = (raw as Record<string, unknown>).platforms;
  if (!platforms || typeof platforms !== "object") return null;
  const pl = platforms as Record<string, { url?: string; signature?: string }>;
  const keys = Object.keys(pl);
  for (const k of keys) {
    if (k.startsWith("windows-")) {
      const a = pl[k];
      if (a?.url && a?.signature) return { url: a.url, signature: a.signature };
    }
  }
  if (keys.length === 1) {
    const a = pl[keys[0]!]!;
    if (a?.url && a?.signature) return { url: a.url, signature: a.signature };
  }
  return null;
}

export function DesktopUpdateBanner() {
  const [pending, setPending] = useState<Update | null>(null);
  const [prepareBusy, setPrepareBusy] = useState(false);
  const [applyBusy, setApplyBusy] = useState(false);
  const [installError, setInstallError] = useState<string | null>(null);
  const [stagedReady, setStagedReady] = useState<{
    path: string;
    version: string;
  } | null>(null);
  const [showRestartModal, setShowRestartModal] = useState(false);
  const installingRef = useRef(false);
  const lastVisibilityRecheckRef = useRef(0);

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
      setInstallError(null);
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

  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState !== "visible") return;
      const now = Date.now();
      if (now - lastVisibilityRecheckRef.current < VISIBILITY_RECHECK_THROTTLE_MS) {
        return;
      }
      lastVisibilityRecheckRef.current = now;
      void runCheck();
    };
    window.addEventListener("focus", onVisible);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.removeEventListener("focus", onVisible);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [runCheck]);

  useEffect(() => {
    let unlisten: UnlistenFn | undefined;
    void listen<HotUpdatePrepareResult>("hot-update-prepare-result", (ev) => {
      const p = ev.payload;
      installingRef.current = false;
      setPrepareBusy(false);
      if (!p?.ok) {
        setInstallError(p?.error ?? "准备更新失败（无详细说明）。");
        return;
      }
      const path = p.stagedNewExe?.trim();
      if (!path) {
        setInstallError("准备成功但未返回安装包路径，请重试。");
        return;
      }
      setStagedReady({ path, version: p.newVersion });
      setInstallError(null);
      setShowRestartModal(true);
    }).then((fn) => {
      unlisten = fn;
    });
    return () => {
      void unlisten?.();
    };
  }, []);

  const invokePrepareArgs = (art: { url: string; signature: string }, version: string) => ({
    downloadUrl: art.url,
    signature: art.signature,
    newVersion: version,
    payload: {
      downloadUrl: art.url,
      signature: art.signature,
      newVersion: version,
    },
  });

  const onPrepareDownload = async () => {
    if (!pending || installingRef.current) return;
    const art = pickPlatformArtifact(pending.rawJson);
    if (!art) {
      setInstallError("无法从更新清单解析当前平台的 url/signature（platforms.*）。");
      return;
    }

    installingRef.current = true;
    setPrepareBusy(true);
    setInstallError(null);

    await updaterDebugLog(
      `spawn_hot_update_prepare START newVersion=${pending.version} url_len=${art.url.length}`
    );

    try {
      await invoke("spawn_hot_update_prepare", invokePrepareArgs(art, pending.version));
      await updaterDebugLog(
        "spawn_hot_update_prepare invoke returned; 等待 hot-update-prepare-result 事件"
      );
    } catch (e) {
      installingRef.current = false;
      setPrepareBusy(false);
      const err =
        e instanceof Error
          ? `${e.name}: ${e.message}\n${e.stack ?? ""}`
          : JSON.stringify(e);
      console.error("[DesktopUpdateBanner] spawn_hot_update_prepare failed:", e);
      await updaterDebugLog(`spawn_hot_update_prepare FAILED: ${err}`);
      const short = e instanceof Error ? e.message : String(e);
      const isHelper =
        short.includes("jachin-updater-helper") || short.includes("热更新助手");
      const isPayloadMismatch =
        short.includes("payload") && short.includes("spawn_hot_update_prepare");
      setInstallError(
        isHelper
          ? short
          : isPayloadMismatch
            ? `${short.slice(0, 220)} 请安装包含本修复的新版桌面 exe。`
            : `${short.slice(0, 200)}（未找到助手时：与主程序同目录放置 jachin-updater-helper.exe）`
      );
    }
  };

  const onConfirmRestart = async () => {
    if (!stagedReady || applyBusy) return;
    setApplyBusy(true);
    setInstallError(null);
    await updaterDebugLog(
      `apply_staged_hot_update_and_exit staged=${stagedReady.path.slice(-48)}`
    );
    try {
      await invoke("apply_staged_hot_update_and_exit", {
        stagedNewExe: stagedReady.path,
        newVersion: stagedReady.version,
      });
      await updaterDebugLog("apply_staged invoke returned (若未退出则可能异常)");
    } catch (e) {
      setApplyBusy(false);
      const short = e instanceof Error ? e.message : String(e);
      setInstallError(short);
      await updaterDebugLog(`apply_staged FAILED: ${short}`);
    }
  };

  if (!pending) return null;

  const busy = prepareBusy || applyBusy;

  return (
    <>
      <div className="shrink-0 z-[100] border-b border-amber-500/30 bg-amber-950/90 px-4 py-2 text-sm text-amber-100 flex flex-col gap-2">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <span>
            发现新版本 <strong className="text-white">{pending.version}</strong>
            。点击「立即更新」将在后台下载并校验安装包，本窗口保持打开；完成后会提示您是否立即重启完成替换。需存在{" "}
            <code className="text-amber-50/90">%LOCALAPPDATA%\com.jachin.desktop</code> 与用户{" "}
            <code className="text-amber-50/90">.jachin</code> 目录。
          </span>
          <div className="flex flex-wrap gap-2 shrink-0">
            {stagedReady && !prepareBusy && (
              <button
                type="button"
                disabled={busy}
                onClick={() => void onConfirmRestart()}
                className="rounded-lg bg-emerald-600/90 px-4 py-1.5 font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
              >
                {applyBusy ? "正在退出并安装…" : "立即重启完成更新"}
              </button>
            )}
            <button
              type="button"
              disabled={busy || !!stagedReady}
              onClick={() => void onPrepareDownload()}
              className="rounded-lg bg-amber-500/90 px-4 py-1.5 font-medium text-slate-950 hover:bg-amber-400 disabled:opacity-50"
            >
              {prepareBusy ? "正在从官网下载并校验…" : stagedReady ? "已就绪" : "立即更新"}
            </button>
          </div>
        </div>
        {stagedReady && !showRestartModal && (
          <p className="text-xs text-emerald-200/90">
            新版本已下载并校验完成。可点击「立即重启完成更新」，或关闭下方对话框后稍后再点横幅上的绿色按钮。
          </p>
        )}
        {installError && (
          <p className="text-xs text-red-300/95 border border-red-500/40 rounded px-2 py-1.5 bg-red-950/50">
            {installError}
          </p>
        )}
        {prepareBusy && (
          <p className="text-xs text-amber-200/85">
            下载与校验进行中，请勿关闭本程序。进度见{" "}
            <code className="text-amber-100/90">D:\zzz\jachin\hot_update_debug.log</code> 中{" "}
            <code className="text-amber-100/90">[updater_helper]</code>。
          </p>
        )}
      </div>

      {showRestartModal && stagedReady && (
        <div
          className="fixed inset-0 z-[200] flex items-center justify-center bg-black/55 px-4"
          role="dialog"
          aria-modal="true"
          aria-labelledby="hot-update-restart-title"
        >
          <div className="max-w-md w-full rounded-xl border border-amber-500/40 bg-slate-900 text-amber-50 shadow-xl p-5 flex flex-col gap-4">
            <h2
              id="hot-update-restart-title"
              className="text-lg font-semibold text-white"
            >
              更新已就绪
            </h2>
            <p className="text-sm text-amber-100/90 leading-relaxed">
              版本 <strong className="text-white">{stagedReady.version}</strong>{" "}
              已下载并通过签名校验。是否立即退出本程序并完成安装？确认后主程序先退出，热更新助手在后台确认无进程占用后再启动安装程序（不会在点击「立即更新」下载阶段关闭主程序）。
            </p>
            <div className="flex flex-wrap gap-2 justify-end">
              <button
                type="button"
                disabled={applyBusy}
                onClick={() => {
                  setShowRestartModal(false);
                }}
                className="rounded-lg border border-amber-500/50 px-4 py-2 text-sm text-amber-100 hover:bg-amber-950/80 disabled:opacity-50"
              >
                稍后
              </button>
              <button
                type="button"
                disabled={applyBusy}
                onClick={() => void onConfirmRestart()}
                className="rounded-lg bg-amber-500 px-4 py-2 text-sm font-medium text-slate-950 hover:bg-amber-400 disabled:opacity-50"
              >
                {applyBusy ? "处理中…" : "立即重启"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
