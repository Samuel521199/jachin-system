/**
 * Sidebar - 侧舷航行桥 (The Bridge)
 * 全息悬浮板 + 导航左侧光柱激活态 + 底部 SystemHeartbeat
 */

import { NavLink } from "react-router-dom";
import { invoke } from "@tauri-apps/api/core";
import { useEffect, useMemo, useState } from "react";
import {
  LayoutDashboard,
  BrainCircuit,
  AppWindow,
  Network,
  Palette,
  Settings,
  Calendar as CalIcon,
  Mic,
  Radar,
  FlaskConical,
  Gamepad2,
  ShieldCheck,
  Briefcase,
  BarChart3,
  MonitorCheck,
  PackagePlus,
  DownloadCloud,
  BookOpen,
} from "lucide-react";
import { cn } from "../utils/cn";
import { SystemHeartbeat } from "./components/SystemHeartbeat";
import { useDesktopUiLang } from "../hooks/useDesktopUiLang";
import { getDesktopConsole } from "../utils/desktopUiI18n";
import { INVENTORY_UPDATED_EVENT } from "../hooks/useUISyncEventSource";

type InstallStatus =
  | "installed"
  | "local_only"
  | "update_available"
  | "repair_needed"
  | "disabled"
  | "not_installed"
  | "blocked";

interface CapabilityInstallItem {
  id: string;
  name: string;
  kind: string;
  enabled: boolean;
  status: InstallStatus | string;
}

type CapabilityGate = {
  ids?: readonly string[];
  prefixes?: readonly string[];
  nameIncludes?: readonly string[];
};

const INSTALLED_STATUSES = new Set(["installed", "local_only", "update_available"]);
const isDevConsoleRuntime = import.meta.env.DEV;

const navItems = [
  { path: "/dashboard", label: "Dashboard", icon: LayoutDashboard, title: "首页：系统状态、快捷操作与最近活动" },
  { path: "/brain", label: "Neural Nexus", icon: BrainCircuit, title: "模型与记忆管理，后端连接状态" },
  {
    path: "/safety-lock",
    labelKey: "safetyLock" as const,
    titleKey: "safetyLockTitle" as const,
    icon: ShieldCheck,
  },
  { path: "/calendar", labelKey: "calendar" as const, titleKey: "calendarTitle" as const, icon: CalIcon },
  { path: "/skills", label: "Skill Matrix", icon: AppWindow, title: "插件与技能，自然语言执行与能力调用" },
  {
    path: "/capability-publish",
    label: "能力发布",
    icon: PackagePlus,
    title: "MCP / Skill 打包、版本迭代与 L1 发布",
  },
  {
    path: "/capability-install",
    label: "能力安装",
    icon: DownloadCloud,
    title: "L3 直连 L1，安装、更新、修复 MCP / Skill",
  },
  {
    path: "/english-vocab",
    label: "英语学习",
    icon: BookOpen,
    title: "英语背词后台：词书选择、学习统计与趋势图",
    capabilityGate: {
      ids: ["com.jachin.skill.english-learning-assistant"],
      prefixes: ["com.jachin.skill.english"],
      nameIncludes: ["english learning assistant"],
    },
  },
  { path: "/network", label: "Jachin Link", icon: Network, title: "网络拓扑与已连接设备列表" },
  {
    path: "/wake",
    labelKey: "wakeMode" as const,
    titleKey: "wakeModeTitle" as const,
    icon: Mic,
  },
  {
    path: "/monitor",
    label: "巡检中枢",
    icon: Radar,
    title: "Kalaroko 全链路多轮巡检、实时日志与 AI 综合分析",
    capabilityGate: {
      ids: ["com.jachin.mcp.kalaroko-monitor", "kalaroko-monitor", "kalaroko_monitor"],
      prefixes: ["com.jachin.kalaroko", "com.jachin.skill.kalaroko", "com.jachin.mcp.kalaroko", "kalaroko-"],
      nameIncludes: ["kalaroko", "巡检"],
    },
  },
  {
    path: "/k11-smoke",
    label: "冒烟测试",
    icon: FlaskConical,
    title: "K11 统合平台 Playwright 冒烟（scripts/test_k11_unified_platform_smoke_playwright.py）",
    capabilityGate: {
      ids: ["com.jachin.k11.smoke", "k11-smoke", "k11_unified_smoke"],
      prefixes: ["com.jachin.k11", "com.jachin.skill.k11", "com.jachin.mcp.k11", "k11-"],
      nameIncludes: ["k11", "冒烟", "smoke"],
    },
  },
  {
    path: "/gameqa",
    label: "游戏 QA",
    icon: Gamepad2,
    title: "游戏 QA / 自动化测试平台：视觉测试、冒烟、回放和规则执行",
    capabilityGate: {
      ids: ["com.jachin.skill.game-qa-automation"],
      prefixes: ["com.jachin.skill.gameqa", "com.jachin.skill.game-qa"],
      nameIncludes: ["游戏 qa", "自动化测试平台"],
    },
  },
  {
    path: "/os-evidence",
    label: "桌面执行",
    icon: MonitorCheck,
    title: "企业桌面执行 Agent：跨 Windows/macOS、飞书、文件、浏览器和办公软件完成真实任务",
    capabilityGate: {
      ids: ["com.jachin.skill.desktop-execution-agent"],
      prefixes: ["com.jachin.skill.desktop", "com.jachin.skill.os"],
      nameIncludes: ["桌面执行"],
    },
  },
  {
    path: "/pmo",
    label: "项目管理",
    icon: Briefcase,
    title: "项目管理：PMO Copilot 触发器与定时任务调度",
    capabilityGate: {
      ids: ["pmo-copilot", "com.jachin.pmo.copilot", "com.jachin.skill.pmo-copilot"],
      prefixes: ["com.jachin.skill.pmo", "pmo-"],
      nameIncludes: ["pmo copilot"],
    },
  },
  {
    path: "/bi",
    label: "BI 增长",
    icon: BarChart3,
    title: "BI 数据增长官：经营分析、留存、充值、游戏经济和战略建议",
    capabilityGate: {
      ids: ["com.jachin.skill.bi-growth-officer"],
      prefixes: ["com.jachin.skill.bi"],
      nameIncludes: ["bi 数据增长官"],
    },
  },
  { path: "/settings", label: "Persona", icon: Palette, title: "形象与声音个性化设置" },
  {
    path: "/preferences",
    labelKey: "preferences" as const,
    titleKey: "preferencesTitle" as const,
    icon: Settings,
  },
] as const;

type NavItem = (typeof navItems)[number];

function navSection(path: string, lang: "zh" | "en"): string {
  const zh = lang === "zh";
  if (path === "/dashboard" || path === "/brain" || path === "/safety-lock" || path === "/calendar") {
    return zh ? "核心中枢" : "CORE";
  }
  if (path === "/skills" || path === "/capability-publish" || path === "/capability-install" || path === "/english-vocab") {
    return zh ? "能力矩阵" : "CAPABILITY";
  }
  if (path === "/network" || path === "/wake" || path === "/monitor" || path === "/k11-smoke") {
    return zh ? "感知与巡检" : "SENSORY";
  }
  if (path === "/gameqa" || path === "/os-evidence" || path === "/pmo" || path === "/bi") {
    return zh ? "任务执行" : "OPERATIONS";
  }
  return zh ? "人格设置" : "PERSONA";
}

function capabilityInstalled(items: CapabilityInstallItem[], gate?: CapabilityGate): boolean {
  if (!gate) return true;
  const ids = new Set((gate.ids ?? []).map((id) => id.trim().toLowerCase()).filter(Boolean));
  const prefixes = (gate.prefixes ?? []).map((id) => id.trim().toLowerCase()).filter(Boolean);
  const names = (gate.nameIncludes ?? []).map((id) => id.trim().toLowerCase()).filter(Boolean);
  return items.some((item) => {
    if (!item.enabled || !INSTALLED_STATUSES.has(item.status)) return false;
    const id = item.id.trim().toLowerCase();
    const name = item.name.trim().toLowerCase();
    return (
      ids.has(id) ||
      prefixes.some((prefix) => id.startsWith(prefix)) ||
      names.some((needle) => name.includes(needle))
    );
  });
}

export function Sidebar() {
  const [lang] = useDesktopUiLang();
  const c = getDesktopConsole(lang);
  const [installedItems, setInstalledItems] = useState<CapabilityInstallItem[]>([]);
  const [installScanReady, setInstallScanReady] = useState(isDevConsoleRuntime);

  useEffect(() => {
    if (isDevConsoleRuntime) return;
    let alive = true;
    const refresh = () => {
      invoke<CapabilityInstallItem[]>("capability_install_local_inventory")
        .then((items) => {
          if (!alive) return;
          setInstalledItems(items ?? []);
          setInstallScanReady(true);
        })
        .catch(() => {
          if (!alive) return;
          setInstalledItems([]);
          setInstallScanReady(true);
        });
    };
    refresh();
    window.addEventListener(INVENTORY_UPDATED_EVENT, refresh);
    return () => {
      alive = false;
      window.removeEventListener(INVENTORY_UPDATED_EVENT, refresh);
    };
  }, []);

  const visibleNavItems = useMemo(() => {
    if (isDevConsoleRuntime) return navItems;
    if (!installScanReady) return navItems.filter((item) => !("capabilityGate" in item));
    return navItems.filter((item) => capabilityInstalled(installedItems, "capabilityGate" in item ? item.capabilityGate : undefined));
  }, [installScanReady, installedItems]);

  const navGroups = useMemo(() => {
    const groups: Array<{ section: string; items: NavItem[] }> = [];
    for (const item of visibleNavItems) {
      const section = navSection(item.path, lang);
      const last = groups[groups.length - 1];
      if (last?.section === section) {
        last.items.push(item);
      } else {
        groups.push({ section, items: [item] });
      }
    }
    return groups;
  }, [visibleNavItems, lang]);

  return (
    <aside
      className={cn(
        "jarvis-sidebar group flex flex-shrink-0 flex-col overflow-hidden transition-[width] duration-300 ease-out",
        "z-10 w-20 border-r border-cyan-200/[0.07] bg-slate-950/58 shadow-[12px_0_44px_rgba(0,0,0,0.24)] backdrop-blur-xl hover:w-72"
      )}
    >
      <div className="jarvis-sidebar-scan" aria-hidden />
      <div className="relative z-10 flex min-w-0 flex-shrink-0 items-center gap-3 border-b border-cyan-200/[0.06] p-4">
        <div
          className="jarvis-sidebar-mark flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-[8px] border border-cyan-200/[0.09] bg-cyan-300/[0.04] shadow-[inset_0_0_18px_rgba(56,189,248,0.025)]"
          style={{ fontFamily: "Orbitron, sans-serif" }}
        >
          <span className="text-sm font-bold text-cyan-100">J</span>
        </div>
        <div className="w-0 overflow-hidden opacity-0 transition-all duration-300 group-hover:w-44 group-hover:opacity-100">
          <h1
            className="whitespace-nowrap text-sm font-semibold tracking-[0.18em] text-cyan-50"
            style={{ fontFamily: "Orbitron, sans-serif" }}
          >
            JACHIN
          </h1>
          <p className="mt-0.5 truncate text-[10px] uppercase tracking-[0.18em] text-slate-500">Omni Console</p>
        </div>
      </div>
      <nav className="relative z-10 min-h-0 flex-1 overflow-y-auto px-2 py-3">
        {navGroups.map((group) => (
          <div key={group.section} className="mb-3 last:mb-0">
            <div className="jarvis-nav-section mb-1 flex items-center gap-2 px-2">
              <span className="h-px w-3 shrink-0 bg-cyan-200/[0.14]" />
              <span className="w-0 overflow-hidden whitespace-nowrap text-[9px] font-semibold uppercase tracking-[0.2em] text-cyan-100/42 opacity-0 transition-all duration-300 group-hover:w-auto group-hover:opacity-100">
                {group.section}
              </span>
              <span className="hidden h-px flex-1 bg-cyan-200/[0.08] group-hover:block" />
            </div>
            <div className="space-y-1.5">
              {group.items.map((item) => {
                const Icon = item.icon;
                const label = "label" in item ? item.label : c.sidebar[item.labelKey];
                const title = "title" in item ? item.title : c.sidebar[item.titleKey];
                const path = item.path;
                return (
                  <NavLink
                    key={path}
                    to={path}
                    end={path === "/dashboard"}
                    title={title}
                    className={({ isActive }) =>
                      cn(
                        "jarvis-nav-item relative flex items-center gap-3 rounded-[8px] border py-2.5 pl-3 pr-2 text-sm font-medium transition-all duration-200",
                        isActive ? "jarvis-nav-active text-cyan-50" : "text-slate-500 hover:text-cyan-100/90"
                      )
                    }
                  >
                    {({ isActive }) => (
                      <>
                        <span className={cn("jarvis-nav-icon flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-[7px]", isActive && "jarvis-nav-icon-active")}>
                          <Icon className="h-[18px] w-[18px] flex-shrink-0" />
                        </span>
                        <span className="min-w-0 flex-1 overflow-hidden whitespace-nowrap opacity-0 transition-all duration-300 group-hover:opacity-100">
                          {label}
                        </span>
                        <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full opacity-0 transition group-hover:opacity-100", isActive ? "bg-cyan-200 shadow-[0_0_10px_rgba(125,211,252,0.75)]" : "bg-cyan-200/18")} />
                      </>
                    )}
                  </NavLink>
                );
              })}
            </div>
          </div>
        ))}
      </nav>
      <SystemHeartbeat />
    </aside>
  );
}
