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

  return (
    <aside
      className={cn(
        "console-fiber-host group flex flex-shrink-0 flex-col overflow-hidden transition-[width] duration-300 ease-out",
        "z-10 w-20 bg-slate-900/50 shadow-[10px_0_40px_rgba(0,0,0,0.55)] backdrop-blur-xl hover:w-64",
        "border-r-0"
      )}
    >
      <div className="flex min-w-0 flex-shrink-0 items-center gap-3 border-b border-cyan-500/15 p-4">
        <div
          className="flex h-8 w-8 flex-shrink-0 items-center justify-center border border-cyan-500/25 bg-gradient-to-br from-rose-500/25 to-cyan-500/20 [clip-path:polygon(0_0,calc(100%-4px)_0,100%_4px,100%_100%,4px_100%,0_calc(100%-4px))]"
          style={{ fontFamily: "Orbitron, sans-serif" }}
        >
          <span className="text-sm font-bold text-rose-400">J</span>
        </div>
        <h1
          className="font-sci-fi w-0 overflow-hidden whitespace-nowrap bg-gradient-to-r from-rose-400 to-rose-600 bg-clip-text font-bold tracking-wider text-transparent opacity-0 transition-all duration-300 group-hover:w-auto group-hover:opacity-100"
          style={{ fontFamily: "Orbitron, sans-serif" }}
        >
          CONSOLE
        </h1>
      </div>
      <nav className="min-h-0 flex-1 space-y-0.5 overflow-y-auto p-2">
        {visibleNavItems.map((item) => {
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
                  "relative flex items-center gap-3 rounded-r-lg border border-y-0 border-r-0 border-transparent py-3 pl-3 pr-2 text-sm font-medium transition-all duration-200",
                  "border-l-2",
                  isActive
                    ? "border-cyan-400/45 bg-cyan-500/[0.08] text-cyan-200/95 shadow-[inset_0_0_20px_rgba(34,211,238,0.06)] drop-shadow-[0_0_6px_rgba(34,211,238,0.25)]"
                    : "border-transparent text-cyan-900/80 hover:border-cyan-500/35 hover:bg-cyan-500/[0.05] hover:text-cyan-300 hover:shadow-[inset_0_0_16px_rgba(34,211,238,0.04)] hover:drop-shadow-[0_0_5px_rgba(34,211,238,0.2)]"
                )
              }
            >
              <Icon className="h-5 w-5 flex-shrink-0" />
              <span className="w-0 overflow-hidden whitespace-nowrap opacity-0 transition-all duration-300 group-hover:w-auto group-hover:opacity-100">
                {label}
              </span>
            </NavLink>
          );
        })}
      </nav>
      <SystemHeartbeat />
    </aside>
  );
}
