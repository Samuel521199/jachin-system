"use client";

import Link from "next/link";

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
  { href: "/console/fleet", label: "Fleet" },
  { href: "/console/pair", label: "Add Agent" },
];

/**
 * 全局导航。Auth.js 已闭环：未登录用户由 middleware 重定向至 `/login`；此处提供显式登录入口。
 */
export default function Navbar() {
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
          <Link
            href="/login"
            className="text-sm text-cyan-400/90 hover:text-cyan-300 transition-colors"
          >
            登录
          </Link>
        </div>
      </div>
    </nav>
  );
}
