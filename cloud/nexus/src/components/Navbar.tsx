"use client";

import Link from "next/link";
import { useSession, signOut } from "next-auth/react";

/**
 * 与落地页顶栏参考图一致：单行从左到右 — Logo → Store → … → Add Agent → 登录（红框内），
 * 「桌面端下载」单独固定在栏最右侧（绿框），不占中间链位置。
 */
const navLinks = [
  { href: "/store", label: "Store" },
  { href: "/dashboard/analytics", label: "审计大屏" },
  { href: "/developer/payouts", label: "收纳中心" },
  { href: "/developer/plugins", label: "我的作品" },
  { href: "/dashboard/admin/review", label: "法律市场" },
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

function desktopDownloadHref(): { href: string; external: boolean } {
  const raw = process.env.NEXT_PUBLIC_DESKTOP_DOWNLOAD_HALL_URL?.trim();
  if (raw && /^https?:\/\//i.test(raw)) {
    return { href: raw, external: true };
  }
  return { href: "/desktop-downloads", external: false };
}

export default function Navbar() {
  const { data: session, status } = useSession();
  const download = desktopDownloadHref();

  const authSlot =
    status === "loading" ? (
      <span className="text-sm text-white/40 w-20 inline-block shrink-0">…</span>
    ) : session?.user ? (
      <div className="flex items-center gap-3 shrink-0">
        <span
          className="text-sm text-white/80 max-w-[160px] truncate sm:max-w-[200px]"
          title={session.user.email ?? session.user.name ?? ""}
        >
          {session.user.name || session.user.email || "已登录"}
        </span>
        <button
          type="button"
          className="text-sm text-cyan-400/90 hover:text-cyan-300 transition-colors shrink-0"
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

  const downloadBtnClass =
    "inline-flex items-center justify-center shrink-0 " +
    "rounded-xl border border-cyan-400/45 bg-cyan-500/5 " +
    "px-3 py-1.5 text-sm font-medium text-cyan-100/95 " +
    "shadow-[0_0_0_1px_rgba(34,211,238,0.12)] " +
    "hover:border-cyan-400/70 hover:bg-cyan-500/10 hover:text-white transition-colors";

  return (
    <nav className="fixed top-0 left-0 right-0 z-50 backdrop-blur-md bg-black/20 border-b border-white/5">
      <div className="mx-auto flex h-16 max-w-[min(100%,96rem)] items-stretch px-6 lg:px-10">
        {/* 红框：一整条横向导航（Logo → … → 登录），窄屏可横向滚动 */}
        <div
          className={
            "flex min-w-0 flex-1 items-center gap-x-5 overflow-x-auto py-1 md:gap-x-6 lg:gap-x-7 " +
            "[scrollbar-width:none] [-ms-overflow-style:none] [&::-webkit-scrollbar]:hidden"
          }
        >
          <Link
            href="/"
            className="shrink-0 text-lg font-semibold tracking-[0.2em] text-white/95 hover:text-white transition-colors"
          >
            JACHIN NEXUS
          </Link>
          {navLinks.map((link) => (
            <Link key={link.href} href={link.href} className={navItemClass}>
              {link.label}
            </Link>
          ))}
          {authSlot}
        </div>

        {/* 绿框：仅「桌面端下载」，贴顶栏最右，与红框用竖线分隔 */}
        <div className="flex shrink-0 items-center border-l border-white/10 pl-4 sm:pl-5 ml-2">
          {download.external ? (
            <a
              href={download.href}
              target="_blank"
              rel="noopener noreferrer"
              className={downloadBtnClass}
            >
              桌面端下载
            </a>
          ) : (
            <Link href={download.href} className={downloadBtnClass}>
              桌面端下载
            </Link>
          )}
        </div>
      </div>
    </nav>
  );
}
