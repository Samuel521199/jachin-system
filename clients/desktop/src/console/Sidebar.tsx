/**
 * Sidebar - 侧舷航行桥 (The Bridge)
 * 全息悬浮板 + 导航左侧光柱激活态 + 底部 SystemHeartbeat
 */

import { NavLink } from "react-router-dom";
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
} from "lucide-react";
import { cn } from "../utils/cn";
import { SystemHeartbeat } from "./components/SystemHeartbeat";
import { useDesktopUiLang } from "../hooks/useDesktopUiLang";
import { getDesktopConsole } from "../utils/desktopUiI18n";

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
  },
  {
    path: "/k11-smoke",
    label: "冒烟测试",
    icon: FlaskConical,
    title: "K11 统合平台 Playwright 冒烟（scripts/test_k11_unified_platform_smoke_playwright.py）",
  },
  {
    path: "/gameqa",
    label: "游戏测试",
    icon: Gamepad2,
    title: "GameQA：本地 MCP 语义测试 / 影子示教（l3_client/local_mcps/gameqa_mcp）",
  },
  {
    path: "/os-evidence",
    label: "OS 证据链",
    icon: MonitorCheck,
    title: "OS Assistant：跨 App 任务 evidence、截图/OCR、报告与发送校验",
  },
  {
    path: "/pmo",
    label: "项目管理",
    icon: Briefcase,
    title: "项目管理：PMO Copilot 触发器与定时任务调度",
  },
  {
    path: "/bi",
    label: "BI 分析",
    icon: BarChart3,
    title: "BI 每日战报：scripts/run_bi_daily_report.py，支持手动启动与北京时间定时",
  },
  { path: "/settings", label: "Persona", icon: Palette, title: "形象与声音个性化设置" },
  {
    path: "/preferences",
    labelKey: "preferences" as const,
    titleKey: "preferencesTitle" as const,
    icon: Settings,
  },
] as const;

export function Sidebar() {
  const [lang] = useDesktopUiLang();
  const c = getDesktopConsole(lang);

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
        {navItems.map((item) => {
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
