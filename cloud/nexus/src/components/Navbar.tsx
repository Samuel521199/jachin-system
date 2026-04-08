"use client";

import Link from "next/link";
import { useSession, signOut } from "next-auth/react";

const navLinks = [
  { href: "/store", label: "Store" },
  { href: "/dashboard/analytics", label: "审计大屏" },
  { href: "/developer/payouts", label: "收益中心" },
  { href: "/developer/plugins", label: "我的作品" },
  { href: "/dashboard/admin/review", label: "法律审核" },
  { href: "/market", label: "Market" },
  { href: "/forge", label: "The Forge" },
  { href: "/plaza", label: "Plaza" },
  { href: "/console", label: "Console" },
  { href: "/console/workspace", label: "工作区" },
  { href: "/console/fleet", label: "Fleet" },
  { href: "/console/pair", label: "Add Agent" },
];

const navItemClass =
  "text-sm text-white/70 hover:text-white/95 transition-colors tracking-wide whitespace-nowrap shrink-0";

/** 默认站内 `/desktop-downloads`，与 Nexus 共用 Session；若需独立域名可设 NEXT_PUBLIC_DESKTOP_DOWNLOAD_HALL_URL */
function desktopDownloadHref(): { href: string; external: boolean } {
  const raw = process.env.NEXT_PUBLIC_DESKTOP_DOWNLOAD_HALL_URL?.trim();
  if (raw && /^https?:\/\//i.test(raw)) {
    return { href: raw, external: true };
  }
  return { href: "/desktop-downloads", external: false };
}

/**
 * 左 Logo | 中间主菜单（独立居中、大间距）| 最右「桌面端下载」圆角框按钮 + 登录/会话
 */
export default function Navbar() {
  const { data: session, status } = useSession();
  const download = desktopDownloadHref();

  const authSlot =
    status === "loading" ? (
      <span className="text-sm text-white/40 w-20 inline-block">…</span>
    ) : session?.user ? (
      <div className="flex items-center gap-3 shrink-0">
        <span
          className="text-sm text-white/80 max-w-[200px] truncate"
          title={session.user.email ?? session.user.name ?? ""}
        >
          {session.user.name || session.user.email || "已登录"}
        </span>
        <button
          type="button"
          className="text-sm text-cyan-400/90 hover:text-cyan-300 transition-colors"
          onClick={() => void signOut({ callbackUrl: "/" })}
        >
          退出
        </button>
      </div>
    ) : (
      <Link
        href="/login"
        className="text-sm text-cyan-400/90 hover:text-cyan-300 transition-colors shrink-0"
      >
        登录
      </Link>
    );

  return (
    <nav className="fixed top-0 left-0 right-0 z-50 backdrop-blur-md bg-black/20 border-b border-white/5">
      <div className="mx-auto flex h-16 max-w-[min(100%,96rem)] items-center gap-4 px-6 lg:px-10">
        <Link
          href="/"
          className="shrink-0 text-lg font-semibold tracking-[0.2em] text-white/95 hover:text-white transition-colors"
        >
          JACHIN NEXUS
        </Link>

        {/* 红框：仅主菜单，居中铺开，间距与 v0.8.99 参考图一致 */}
        <div className="flex min-w-0 flex-1 items-center justify-center overflow-x-auto py-1 [scrollbar-width:none] [-ms-overflow-style:none] [&::-webkit-scrollbar]:hidden">
          <div className="flex items-center justify-center gap-x-8 md:gap-x-10 lg:gap-x-12 xl:gap-x-14">
            {navLinks.map((link) => (
              <Link key={link.href} href={link.href} className={navItemClass}>
                {link.label}
              </Link>
            ))}
          </div>
        </div>

        {/* 蓝框：圆角矩形按钮 + 登录（不与主菜单混在同一 flex gap 里） */}
        <div className="flex shrink-0 items-center gap-5 pl-2">
          {download.external ? (
            <a
              href={download.href}
              target="_blank"
              rel="noopener noreferrer"
              className="
              inline-flex items-center justify-center shrink-0
              rounded-xl border border-cyan-400/45 bg-cyan-500/5
              px-4 py-1.5 text-sm font-medium text-cyan-100/95
              shadow-[0_0_0_1px_rgba(34,211,238,0.12)]
              hover:border-cyan-400/70 hover:bg-cyan-500/10 hover:text-white
              transition-colors
            "
            >
              桌面端下载
            </a>
          ) : (
            <Link
              href={download.href}
              className="
              inline-flex items-center justify-center shrink-0
              rounded-xl border border-cyan-400/45 bg-cyan-500/5
              px-4 py-1.5 text-sm font-medium text-cyan-100/95
              shadow-[0_0_0_1px_rgba(34,211,238,0.12)]
              hover:border-cyan-400/70 hover:bg-cyan-500/10 hover:text-white
              transition-colors
            "
            >
              桌面端下载
            </Link>
          )}
          {authSlot}
        </div>
      </div>
    </nav>
  );
}
