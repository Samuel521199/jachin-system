/**
 * LiveTile - 技能磁贴：名称、最近执行状态、悬停显示 Permission X-Ray（占位）
 */

import React, { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { AppWindow, Play, Loader2, FileText, Globe, Cpu, Shield, Trash2, Settings } from "lucide-react";
import { cn } from "../../utils/cn";

/** 占位：后端暂无权限数据时使用的 mock 权限类型 */
const MOCK_PERMISSIONS = [
  { id: "file", label: "文件", Icon: FileText },
  { id: "network", label: "网络", Icon: Globe },
  { id: "system", label: "系统", Icon: Cpu },
  { id: "sandbox", label: "沙箱", Icon: Shield },
] as const;

const ICON_BY_ID: Record<string, React.ComponentType<{ className?: string }>> = {
  file: FileText,
  network: Globe,
  system: Cpu,
  sandbox: Shield,
};

export interface LiveTileSkill {
  skill_id: string;
  name: string;
  version?: string;
  capabilities?: Array<{ name?: string; description?: string }>;
  /** L2 inventory 目录名，卸载时使用 */
  item_id?: string;
}

export function LiveTile({
  skill,
  lastResult,
  lastStatus,
  isExecuting,
  onExecute,
  onExpand,
  onUninstall,
  onSettings,
  permissions,
  liveStatus,
  className,
}: {
  skill: LiveTileSkill;
  lastResult?: string | null;
  lastStatus?: "idle" | "success" | "error";
  isExecuting?: boolean;
  onExecute?: (capName: string) => void;
  onExpand?: () => void;
  onUninstall?: () => void;
  onSettings?: () => void;
  /** 后端下发的权限列表，有则替代 mock 占位 */
  permissions?: Array<{ id: string; label: string }>;
  /** 实时状态文案，如「已执行 3 次」「上次 12:30」 */
  liveStatus?: string | null;
  className?: string;
}) {
  const [showPermissions, setShowPermissions] = useState(false);
  const caps = skill.capabilities ?? [];
  const capNames = caps.map((c) => (c.name as string) || (typeof c === "string" ? c : "")).filter(Boolean);
  const displayPermissions = permissions?.length ? permissions : MOCK_PERMISSIONS.slice(0, 3).map(({ id, label }) => ({ id, label }));

  return (
    <motion.div
      layout
      className={cn(
        "glass-panel glass-panel-hover rounded-xl overflow-hidden flex flex-col min-h-[120px]",
        className
      )}
      onMouseEnter={() => setShowPermissions(true)}
      onMouseLeave={() => setShowPermissions(false)}
      initial={{ opacity: 0, scale: 0.96 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 0.2 }}
    >
      <button
        type="button"
        onClick={onExpand}
        className="flex-1 flex flex-col items-stretch text-left p-4 min-h-0"
      >
        <div className="flex items-center gap-2 flex-shrink-0">
          <AppWindow className="w-4 h-4 text-rose-400/80 flex-shrink-0" />
          <span className="font-mono text-sm font-medium text-white truncate">{skill.name}</span>
          {skill.version != null && (
            <span className="text-[10px] text-slate-500 flex-shrink-0">v{skill.version}</span>
          )}
        </div>
        <div className="mt-2 flex-1 min-h-0">
          {lastResult != null && lastResult !== "" ? (
            <p className="text-[11px] text-slate-400 line-clamp-2 font-mono" title={lastResult}>
              {lastStatus === "error" ? (
                <span className="text-amber-400/90">错误</span>
              ) : (
                <span className="text-cyan-400/90">已执行</span>
              )}
              {" · "}
              {lastResult.length > 200 ? `${lastResult.slice(0, 200)}…` : lastResult}
            </p>
          ) : (
            <p className="text-[11px] text-slate-500 font-mono">
              {liveStatus != null && liveStatus !== "" ? (
                liveStatus
              ) : capNames.length > 0 ? (
                `${capNames.length} 项能力`
              ) : (
                "—"
              )}
            </p>
          )}
        </div>
      </button>

      {/* 底部：执行按钮 + 卸载 + Permission X-Ray 占位（relative z-10 确保可点击） */}
      <div className="relative z-10 flex-shrink-0 flex items-center justify-between gap-2 px-4 py-2 border-t border-white/10 bg-black/20" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-2">
          {capNames.length > 0 && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onExecute?.(capNames[0]);
              }}
              disabled={isExecuting}
              className="flex items-center gap-1 px-2 py-1 rounded text-[10px] font-mono bg-rose-600/80 hover:bg-rose-500 text-white disabled:opacity-50"
            >
              {isExecuting ? <Loader2 className="w-3 h-3 animate-spin" /> : <Play className="w-3 h-3" />}
              执行
            </button>
          )}
          {onSettings && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onSettings();
              }}
              className="p-1.5 rounded text-slate-500 hover:text-cyan-400 hover:bg-cyan-500/20 transition-colors"
              title="技能设置"
            >
              <Settings className="w-3.5 h-3.5" />
            </button>
          )}
          {onUninstall && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onUninstall();
              }}
              className="p-1.5 rounded text-slate-500 hover:text-rose-400 hover:bg-rose-500/20 transition-colors"
              title="卸载技能"
            >
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
        <div className="relative flex items-center gap-1">
          <AnimatePresence>
            {showPermissions && (
              <motion.div
                initial={{ opacity: 0, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: 4 }}
                className="flex items-center gap-1.5 text-slate-500"
                title={permissions?.length ? "权限" : "权限占位（后端暂无权限数据）"}
              >
                {displayPermissions.slice(0, 5).map(({ id, label }) => {
                  const Icon = ICON_BY_ID[id] ?? Shield;
                  return (
                    <span
                      key={id}
                      className="flex items-center gap-0.5 text-[10px] font-mono text-slate-500"
                      title={label}
                    >
                      <Icon className="w-3 h-3" />
                    </span>
                  );
                })}
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>
    </motion.div>
  );
}
