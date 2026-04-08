/**
 * 配对后使用 ~/.jachin/nexus_config.json 的 access_token（Rust 注入 updater 请求头）检查 Layer 1 是否有新版本。
 * 安装包由服务端预签名 URL 拉取；用户数据在 ~/.jachin 等目录，与可执行文件热替换独立。
 */
import { useCallback, useEffect, useState } from "react";
import { check, type Update } from "@tauri-apps/plugin-updater";

const CHECK_INTERVAL_MS = 4 * 60 * 60 * 1000;

export function DesktopUpdateBanner() {
  const [pending, setPending] = useState<Update | null>(null);
  const [busy, setBusy] = useState(false);

  const runCheck = useCallback(async () => {
    try {
      const u = await check();
      setPending(u);
    } catch (e) {
      console.warn("[DesktopUpdateBanner] check failed:", e);
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
    try {
      await pending.downloadAndInstall();
    } catch (e) {
      console.error("[DesktopUpdateBanner] install failed:", e);
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
