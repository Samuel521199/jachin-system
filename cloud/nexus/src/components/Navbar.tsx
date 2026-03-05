"use client";

import Link from "next/link";

const navLinks = [
  { href: "/market", label: "Market" },
  { href: "/forge", label: "The Forge" },
  { href: "/plaza", label: "Plaza" },
  { href: "/console", label: "Console" },
  { href: "/console/fleet", label: "Fleet" },
  { href: "/console/pair", label: "Add Agent" },
];

/**
 * 已脱离 Supabase Auth，展示演示模式。
 * 后续可接入 Auth.js 实现登录/登出。
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
          <span className="text-sm text-white/60">演示模式</span>
        </div>
      </div>
    </nav>
  );
}
