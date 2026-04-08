"use client";

import { signOut } from "next-auth/react";

export type ReleaseRow = {
  version: string;
  notes: string;
  pub_date: string;
  platforms: string[];
};

const PLATFORM_LABEL: Record<string, string> = {
  "windows-x86_64": "Windows (x64)",
  "darwin-x86_64": "macOS (Intel)",
  "darwin-aarch64": "macOS (Apple Silicon)",
  "linux-x86_64": "Linux (x64)",
};

function labelFor(key: string) {
  return PLATFORM_LABEL[key] ?? key;
}

function downloadHref(version: string, platform: string) {
  const q = new URLSearchParams({ version, platform });
  return `/api/downloads/generate-link?${q.toString()}`;
}

export function DownloadHall({
  latest,
  history,
  userEmail,
}: {
  latest: ReleaseRow | null;
  history: ReleaseRow[];
  userEmail?: string | null;
}) {
  return (
    <div className="mx-auto max-w-4xl px-4 pb-20 pt-24">
      <header className="mb-10 flex flex-col gap-4 border-b border-white/10 pb-8 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-white">
            Jachin Nexus 桌面端发行大厅
          </h1>
          <p className="mt-1 text-sm text-white/50">
            私有化分发 · 短效预签名链接 · 与 Nexus 账号同源（无需单独登录下载站）
          </p>
        </div>
        <div className="flex items-center gap-3">
          {userEmail ? (
            <span className="max-w-[220px] truncate text-sm text-white/60" title={userEmail}>
              {userEmail}
            </span>
          ) : null}
          <button
            type="button"
            className="rounded-md border border-white/20 bg-transparent px-3 py-1.5 text-sm text-white/90 hover:bg-white/10"
            onClick={() => void signOut({ callbackUrl: "/login" })}
          >
            退出
          </button>
        </div>
      </header>

      {!latest ? (
        <p className="rounded-lg border border-white/10 bg-white/[0.03] px-4 py-8 text-center text-white/50">
          暂无发布记录。请在 Nexus 管理端登记 <code className="text-cyan-400/90">desktop_app_releases</code>{" "}
          并上传产物到 MinIO。
        </p>
      ) : (
        <section className="mb-14">
          <h2 className="mb-2 text-lg font-medium text-cyan-400/90">最新版本</h2>
          <div className="rounded-xl border border-white/10 bg-gradient-to-b from-white/[0.06] to-transparent p-6">
            <div className="mb-4 flex flex-wrap items-baseline gap-3">
              <span className="font-mono text-3xl font-semibold text-white">{latest.version}</span>
              <span className="text-xs text-white/40">
                {new Date(latest.pub_date).toLocaleString()}
              </span>
            </div>
            {latest.notes ? (
              <pre className="mb-6 whitespace-pre-wrap font-sans text-sm leading-relaxed text-white/70">
                {latest.notes}
              </pre>
            ) : null}
            <div className="flex flex-wrap gap-3">
              {latest.platforms.map((p) => (
                <a
                  key={p}
                  href={downloadHref(latest.version, p)}
                  className="inline-flex items-center rounded-lg border border-cyan-500/40 bg-cyan-500/10 px-4 py-2 text-sm font-medium text-cyan-100 hover:bg-cyan-500/20"
                >
                  下载 {labelFor(p)}
                </a>
              ))}
            </div>
          </div>
        </section>
      )}

      <section>
        <h2 className="mb-4 text-lg font-medium text-white/90">历史版本</h2>
        <ul className="space-y-3">
          {history.map((r) => (
            <li
              key={r.version}
              className="rounded-lg border border-white/10 bg-white/[0.02] px-4 py-4"
            >
              <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                <span className="font-mono text-lg text-cyan-300">{r.version}</span>
                <span className="text-xs text-white/40">
                  {new Date(r.pub_date).toLocaleString()}
                </span>
              </div>
              <div className="flex flex-wrap gap-2">
                {r.platforms.map((p) => (
                  <a
                    key={p}
                    href={downloadHref(r.version, p)}
                    className="rounded border border-white/15 px-3 py-1 text-xs text-white/80 hover:bg-white/5"
                  >
                    {labelFor(p)}
                  </a>
                ))}
              </div>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
