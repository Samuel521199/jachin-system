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

/**
 * 全局导航。未登录由 middleware 拦受保护路由；右上角用 `useSession()` 反映真实会话（此前写死「登录」导致已登录仍显示未登录）。
 */
export default function Navbar() {
  const { data: session, status } = useSession();

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
        className="text-sm text-cyan-400/90 hover:text-cyan-300 transition-colors"
      >
        登录
      </Link>
    );

  return (
    <nav className="fixed top-0 left-0 right-0 z-50 backdrop-blur-md bg-black/20 border-b border-white/5">
      <div className="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
        <Link
          href="/"
          className="text-lg font-semibold tracking-[0.2em] text-white/95 hover:text-white transition-colors"
        >
          JACHIN NEXUS
        </Link>
        <div className="flex items-center gap-8">
          {navLinks.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              className="text-sm text-white/70 hover:text-white/95 transition-colors tracking-wide"
            >
              {link.label}
            </Link>
          ))}
          {authSlot}
        </div>
      </div>
    </nav>
  );
}
