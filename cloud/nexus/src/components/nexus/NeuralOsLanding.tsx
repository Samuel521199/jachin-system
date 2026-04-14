"use client";

import Link from "next/link";
import { motion } from "framer-motion";
import type { LucideIcon } from "lucide-react";
import {
  Activity,
  Archive,
  Bot,
  Box,
  Hammer,
  Layout,
  LayoutGrid,
  Layers,
  Orbit,
  Scale,
  ShoppingBag,
  Terminal,
} from "lucide-react";
import { useSession, signOut } from "next-auth/react";
import { useEffect, useRef, useState } from "react";
import { NexusLanguageMenu } from "@/components/NexusLanguageMenu";
import { useNexusUiLang } from "@/components/NexusUiLangProvider";
import { nexusLanding, nexusNav } from "@/lib/nexus-ui-i18n";

function desktopDownloadHref(): { href: string; external: boolean } {
  const raw = process.env.NEXT_PUBLIC_DESKTOP_DOWNLOAD_HALL_URL?.trim();
  if (raw && /^https?:\/\//i.test(raw)) {
    return { href: raw, external: true };
  }
  return { href: "/desktop-downloads", external: false };
}

/** Dock：文案键与 `nexusNav` 对齐 */
const dockItems: {
  href: string;
  navKey: keyof (typeof nexusNav)["zh"];
  Icon: LucideIcon;
}[] = [
  { href: "/store", navKey: "store", Icon: ShoppingBag },
  { href: "/dashboard/analytics", navKey: "analytics", Icon: Activity },
  { href: "/developer/payouts", navKey: "payouts", Icon: Archive },
  { href: "/developer/plugins", navKey: "plugins", Icon: Layers },
  { href: "/dashboard/admin/review", navKey: "legal", Icon: Scale },
  { href: "/market", navKey: "market", Icon: Orbit },
  { href: "/forge", navKey: "forge", Icon: Hammer },
  { href: "/plaza", navKey: "plaza", Icon: LayoutGrid },
  { href: "/console", navKey: "console", Icon: Terminal },
  { href: "/console/workspace", navKey: "workspace", Icon: Layout },
  { href: "/console/fleet", navKey: "fleet", Icon: Box },
  { href: "/console/pair", navKey: "pair", Icon: Bot },
];

function NeuralOsTopbar() {
  const { lang } = useNexusUiLang();
  const { data: session, status } = useSession();
  const download = desktopDownloadHref();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menuOpen) return;
    const onDoc = (e: MouseEvent) => {
      if (!menuRef.current?.contains(e.target as Node)) setMenuOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [menuOpen]);

  const initial =
    session?.user?.name?.charAt(0)?.toUpperCase() ||
    session?.user?.email?.charAt(0)?.toUpperCase() ||
    "?";

  const t = nexusLanding[lang];
  const downloadEl = download.external ? (
    <a
      href={download.href}
      target="_blank"
      rel="noopener noreferrer"
      className={
        "rounded-full border border-cyan-500/30 px-5 py-2 text-sm font-medium text-cyan-400 " +
        "transition-all hover:border-cyan-400 hover:bg-cyan-500/10"
      }
    >
      {t.download}
    </a>
  ) : (
    <Link
      href={download.href}
      className={
        "rounded-full border border-cyan-500/30 px-5 py-2 text-sm font-medium text-cyan-400 " +
        "transition-all hover:border-cyan-400 hover:bg-cyan-500/10"
      }
    >
      {t.download}
    </Link>
  );

  return (
    <header className="absolute left-0 right-0 top-0 z-50 flex items-center justify-between px-6 py-6 sm:px-10">
      <Link
        href="/"
        className="text-lg font-extrabold tracking-[0.2em] text-white transition-opacity hover:opacity-90"
      >
        JACHIN NEXUS
      </Link>

      <div className="flex items-center gap-6">
        <div className="flex items-center gap-4">
          <NexusLanguageMenu />
          {downloadEl}
        </div>
        {status === "loading" ? (
          <span className="h-9 w-9 rounded-full bg-white/10" />
        ) : session?.user ? (
          <div ref={menuRef} className="relative">
            <button
              type="button"
              aria-expanded={menuOpen}
              className={
                "flex h-9 w-9 items-center justify-center rounded-full border border-white/20 " +
                "bg-gradient-to-br from-violet-600/50 to-cyan-600/35 text-sm font-bold text-white " +
                "shadow-[0_0_20px_rgba(34,211,238,0.15)] transition hover:border-cyan-400/40"
              }
              onClick={() => setMenuOpen((o) => !o)}
              title={session.user.email ?? session.user.name ?? ""}
            >
              {initial}
            </button>
            {menuOpen && (
              <div
                className={
                  "absolute right-0 top-[calc(100%+10px)] z-[60] min-w-[200px] rounded-xl " +
                  "border border-white/10 bg-black/90 py-2 shadow-2xl backdrop-blur-xl"
                }
              >
                <p className="truncate border-b border-white/10 px-4 py-2 text-xs text-white/50">
                  {session.user.email}
                </p>
                <button
                  type="button"
                  className="w-full px-4 py-2.5 text-left text-sm text-cyan-400 hover:bg-white/5"
                  onClick={() => {
                    setMenuOpen(false);
                    void signOut({ callbackUrl: "/" });
                  }}
                >
                  {t.signOut}
                </button>
              </div>
            )}
          </div>
        ) : (
          <Link
            href="/login"
            className="text-sm font-medium text-cyan-400/90 transition-colors hover:text-cyan-300"
          >
            {t.login}
          </Link>
        )}
      </div>
    </header>
  );
}

function DockIconButton({
  href,
  name,
  Icon,
}: {
  href: string;
  name: string;
  Icon: LucideIcon;
}) {
  return (
    <Link
      href={href}
      title={name}
      className="group relative inline-flex shrink-0 flex-col items-center justify-end outline-none"
    >
      {/* macOS 风格气泡：纯 CSS group-hover；文案必须用独立节点渲染，避免被 overflow 误裁时无兜底 */}
      <div
        role="tooltip"
        className={
          "pointer-events-none absolute bottom-full left-1/2 z-[70] mb-3 origin-bottom -translate-x-1/2 " +
          "scale-95 whitespace-nowrap rounded-md border border-white/10 bg-[#1a1a1a]/90 " +
          "px-2.5 py-1 text-xs font-medium tracking-wide text-white shadow-xl backdrop-blur-md " +
          "opacity-0 transition-all duration-200 group-hover:scale-100 group-hover:opacity-100"
        }
      >
        <span className="block text-white">{name}</span>
      </div>
      <motion.div
        className={
          "flex h-12 w-12 cursor-pointer items-center justify-center rounded-xl text-gray-400 " +
          "transition-colors group-hover:bg-white/5 group-hover:text-cyan-400"
        }
        whileHover={{ scale: 1.2 }}
        transition={{ type: "spring", stiffness: 400, damping: 22 }}
      >
        <Icon className="h-6 w-6" strokeWidth={1.5} />
      </motion.div>
    </Link>
  );
}

function NeuralDock() {
  const { lang } = useNexusUiLang();
  return (
    <nav
      aria-label="全息导航 Dock"
      className={
        "fixed bottom-8 left-1/2 z-50 max-w-[calc(100vw-1.5rem)] -translate-x-1/2 overflow-visible " +
        "rounded-2xl border border-white/10 bg-black/40 shadow-2xl backdrop-blur-2xl sm:bottom-10"
      }
    >
      {/*
        横向滚动只放在内层；外层 nav 不设 overflow-x-auto，否则会把向上伸出的 tooltip 整块裁掉，看起来像「空气泡」。
        pt-14 在条内预留气泡高度，不改变 Dock 贴底位置。
      */}
      <div
        className={
          "flex max-w-full items-end gap-1 overflow-x-auto px-3 pb-3 pt-14 sm:gap-2 sm:px-4 " +
          "[scrollbar-width:none] [-ms-overflow-style:none] [&::-webkit-scrollbar]:hidden"
        }
      >
        {dockItems.map(({ href, navKey, Icon }) => (
          <DockIconButton key={href} href={href} name={nexusNav[lang][navKey]} Icon={Icon} />
        ))}
      </div>
    </nav>
  );
}

/**
 * Neural OS 全息桌面首页：深渊点阵背景 + 极简穹顶 + 液态玻璃 Hero + 底部 Dock。
 */
export default function NeuralOsLanding() {
  const { lang } = useNexusUiLang();

  return (
    <div className="relative flex min-h-screen flex-col overflow-hidden bg-[#030305] font-sans">
      {/* 极细星尘点阵 */}
      <div
        className={
          "pointer-events-none absolute inset-0 bg-[radial-gradient(rgba(255,255,255,0.06)_1px,transparent_1px)] " +
          "bg-[size:24px_24px] [mask-image:radial-gradient(ellipse_60%_60%_at_50%_50%,#000_70%,transparent_100%)]"
        }
        aria-hidden
      />
      {/* 神经元极光弥散 */}
      <div
        className={
          "pointer-events-none absolute left-1/2 top-1/2 h-[50vh] w-[70vw] -translate-x-1/2 -translate-y-1/2 " +
          "rounded-[100%] bg-gradient-to-r from-purple-900/30 via-cyan-900/20 to-blue-900/30 blur-[120px]"
        }
        aria-hidden
      />

      <NeuralOsTopbar />

      <main className="relative z-10 flex flex-1 flex-col items-center justify-center px-6 pb-32 pt-24 sm:pb-36">
        <div className="mx-auto max-w-4xl text-center">
          <h1 className="mb-4 bg-gradient-to-r from-purple-400 via-cyan-400 to-blue-500 bg-clip-text text-5xl font-black tracking-tight text-transparent sm:text-6xl md:text-7xl">
            The Ether of Intelligence.
          </h1>
          <p className="mb-12 text-sm font-light tracking-widest text-gray-400 sm:text-base">
            Download Skills. Update Soul. Keep your Privacy.
          </p>

          <div className="flex flex-col items-center justify-center gap-6 sm:flex-row">
            <Link
              href="/market"
              className={
                "relative rounded-full border border-white/10 bg-white/[0.03] px-8 py-3.5 text-center " +
                "font-medium text-white shadow-[0_0_30px_rgba(168,85,247,0.2)] backdrop-blur-md " +
                "transition-all duration-300 hover:border-white/30 hover:bg-white/[0.08] " +
                "hover:shadow-[0_0_40px_rgba(0,240,255,0.35)]"
              }
            >
              {nexusLanding[lang].primaryBtn}
            </Link>
            <Link
              href="/forge"
              className={
                "rounded-full border border-white/10 bg-transparent px-8 py-3.5 text-center " +
                "font-medium text-gray-400 transition-all duration-300 hover:border-white/30 " +
                "hover:text-white"
              }
            >
              {nexusLanding[lang].secondaryBtn}
            </Link>
          </div>
        </div>
      </main>

      <NeuralDock />
    </div>
  );
}
