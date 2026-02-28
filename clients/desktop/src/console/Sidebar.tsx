/**
 * Sidebar - 悬浮玻璃侧栏 (The Bridge)
 * 默认仅图标 w-20，悬停展开 w-64，底部 SystemHeartbeat
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
} from "lucide-react";
import { cn } from "../utils/cn";
import { SystemHeartbeat } from "./components/SystemHeartbeat";

const navItems = [
  { path: "/dashboard", label: "Dashboard", icon: LayoutDashboard, title: "首页：系统状态、快捷操作与最近活动" },
  { path: "/brain", label: "Neural Nexus", icon: BrainCircuit, title: "模型与记忆管理，后端连接状态" },
  { path: "/calendar", label: "日历", icon: CalIcon, title: "事件、提醒、待办，支持循环" },
  { path: "/skills", label: "Skill Matrix", icon: AppWindow, title: "插件与技能，自然语言执行与能力调用" },
  { path: "/network", label: "Jachin Link", icon: Network, title: "网络拓扑与已连接设备列表" },
  { path: "/wake", label: "唤醒模式", icon: Mic, title: "设置唤醒词/名字，启动唤醒监听（模式 B）" },
  { path: "/settings", label: "Persona", icon: Palette, title: "形象与声音个性化设置" },
  { path: "/preferences", label: "设置", icon: Settings, title: "AI 模式与运行模式" },
] as const;

export function Sidebar() {
  return (
    <aside
      className={cn(
        "group flex-shrink-0 flex flex-col transition-[width] duration-300 ease-out overflow-hidden z-10",
        "glass-panel rounded-none border-r border-white/10",
        "w-20 hover:w-64"
      )}
    >
      <div className="flex-shrink-0 p-4 border-b border-white/10 flex items-center gap-3 min-w-0">
        <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-rose-500/30 to-cyan-500/30 flex items-center justify-center flex-shrink-0 border border-white/10">
          <span className="text-sm font-bold text-rose-400" style={{ fontFamily: "Orbitron, sans-serif" }}>J</span>
        </div>
        <h1
          className="font-sci-fi font-bold tracking-wider text-transparent bg-clip-text bg-gradient-to-r from-rose-400 to-rose-600 whitespace-nowrap overflow-hidden opacity-0 w-0 group-hover:opacity-100 group-hover:w-auto transition-all duration-300"
          style={{ fontFamily: "Orbitron, sans-serif" }}
        >
          CONSOLE
        </h1>
      </div>
      <nav className="flex-1 p-3 space-y-1 overflow-y-auto min-h-0">
        {navItems.map(({ path, label, icon: Icon, title }) => (
          <NavLink
            key={path}
            to={path}
            end={path === "/dashboard"}
            title={title}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-3 px-3 py-3 rounded-xl text-sm font-medium transition-all duration-200",
                "border border-transparent",
                isActive
                  ? "bg-rose-500/20 text-rose-400 border-rose-500/40 shadow-[0_0_20px_rgba(244,63,94,0.15)]"
                  : "text-slate-400 hover:bg-white/10 hover:text-slate-200 hover:border-white/10"
              )
            }
          >
            <Icon className="w-5 h-5 flex-shrink-0" />
            <span className="whitespace-nowrap overflow-hidden opacity-0 w-0 group-hover:opacity-100 group-hover:w-auto transition-all duration-300">
              {label}
            </span>
          </NavLink>
        ))}
      </nav>
      <SystemHeartbeat />
    </aside>
  );
}
