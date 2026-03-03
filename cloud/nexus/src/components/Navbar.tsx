"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { createClient } from "@/lib/supabase-auth/client";
import { LogOut, LogIn } from "lucide-react";

const navLinks = [
  { href: "/market", label: "Market" },
  { href: "/forge", label: "The Forge" },
  { href: "/plaza", label: "Plaza" },
  { href: "/console", label: "Console" },
  { href: "/console/fleet", label: "Fleet" },
  { href: "/console/pair", label: "Add Agent" },
];

export default function Navbar() {
  const [user, setUser] = useState<{ email?: string; user_metadata?: { email?: string } } | null>(null);

  useEffect(() => {
    const supabase = createClient();
    if (!supabase) return;
    supabase.auth.getUser().then(({ data: { user: u } }) => setUser(u ?? null));
    const { data: { subscription } } = supabase.auth.onAuthStateChange((_e, session) =>
      setUser(session?.user ?? null)
    );
    return () => subscription.unsubscribe();
  }, []);

  const handleLogout = async () => {
    const supabase = createClient();
    if (supabase) {
      await supabase.auth.signOut();
      window.location.href = "/login";
    }
  };

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
          {user ? (
            <div className="flex items-center gap-4">
              <span className="text-sm text-white/60 truncate max-w-[160px]" title={user.email}>
                {user.email ?? user.user_metadata?.email ?? "已登录"}
              </span>
              <button
                onClick={handleLogout}
                className="flex items-center gap-2 text-sm text-white/60 hover:text-cyan-400 transition-colors"
              >
                <LogOut className="w-4 h-4" />
                登出
              </button>
            </div>
          ) : (
            <Link
              href="/login"
              className="flex items-center gap-2 text-sm text-cyan-400 hover:text-cyan-300 transition-colors"
            >
              <LogIn className="w-4 h-4" />
              登录
            </Link>
          )}
        </div>
      </div>
    </nav>
  );
}
