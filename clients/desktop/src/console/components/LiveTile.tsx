import React, { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { AppWindow, Play, Loader2, FileText, Globe, Cpu, Shield, Trash2, Settings, EyeOff, Radio } from "lucide-react";
import { cn } from "../../utils/cn";

const FALLBACK_PERMISSIONS = [
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
  onHide,
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
  onHide?: () => void;
  onSettings?: () => void;
  permissions?: Array<{ id: string; label: string }>;
  liveStatus?: string | null;
  className?: string;
}) {
  const [showPermissions, setShowPermissions] = useState(false);
  const caps = skill.capabilities ?? [];
  const capNames = caps.map((c) => (c.name as string) || (typeof c === "string" ? c : "")).filter(Boolean);
  const displayPermissions = permissions?.length
    ? permissions
    : FALLBACK_PERMISSIONS.slice(0, 3).map(({ id, label }) => ({ id, label }));
  const hasResult = lastResult != null && lastResult !== "";
  const statusTone =
    lastStatus === "error"
      ? "text-amber-200 border-amber-300/20 bg-amber-300/[0.08]"
      : hasResult
        ? "text-emerald-200 border-emerald-300/20 bg-emerald-300/[0.08]"
        : "text-cyan-100/80 border-cyan-200/[0.08] bg-cyan-300/[0.025]";

  return (
    <motion.div
      layout
      className={cn(
        "jarvis-tile group relative flex min-h-[138px] flex-col overflow-hidden rounded-[8px] border border-cyan-200/[0.075] bg-cyan-300/[0.018] shadow-[inset_0_0_28px_rgba(56,189,248,0.02)] transition-all duration-200 hover:border-cyan-200/[0.16] hover:bg-cyan-300/[0.032]",
        className
      )}
      onMouseEnter={() => setShowPermissions(true)}
      onMouseLeave={() => setShowPermissions(false)}
      initial={{ opacity: 0, scale: 0.97 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 0.22 }}
    >
      <button type="button" onClick={onExpand} className="relative z-10 flex flex-1 flex-col items-stretch p-4 text-left">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-[7px] border border-cyan-200/[0.09] bg-cyan-300/[0.035] text-cyan-100/90">
                <AppWindow className="h-3.5 w-3.5" />
              </span>
              <div className="min-w-0">
                <div className="truncate text-sm font-semibold text-slate-100">{skill.name}</div>
                <div className="mt-0.5 flex items-center gap-2 font-mono text-[10px] uppercase tracking-[0.08em] text-slate-500">
                  <span>{capNames.length || 0} caps</span>
                  {skill.version != null && <span>v{skill.version}</span>}
                </div>
              </div>
            </div>
          </div>
          <span className={cn("rounded-full border px-2 py-0.5 font-mono text-[10px]", statusTone)}>
            {lastStatus === "error" ? "ALERT" : hasResult ? "DONE" : "READY"}
          </span>
        </div>

        <div className="mt-4 min-h-[36px]">
          {hasResult ? (
            <p className="line-clamp-2 font-mono text-[11px] leading-5 text-slate-400" title={lastResult ?? ""}>
              {lastResult && lastResult.length > 200 ? `${lastResult.slice(0, 200)}...` : lastResult}
            </p>
          ) : (
            <p className="line-clamp-2 text-xs leading-5 text-slate-400">
              {liveStatus != null && liveStatus !== "" ? liveStatus : capNames.length > 0 ? `${capNames.length} 项能力待命` : "能力元数据待同步"}
            </p>
          )}
        </div>
      </button>

      <div className="relative z-10 flex items-center justify-between gap-3 border-t border-cyan-200/[0.055] bg-slate-950/22 px-4 py-2.5" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-1.5">
          {capNames.length > 0 && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onExecute?.(capNames[0]);
              }}
              disabled={isExecuting}
              className="inline-flex h-7 items-center gap-1.5 rounded-[7px] border border-cyan-200/[0.13] bg-cyan-300/[0.05] px-2.5 font-mono text-[10px] text-cyan-50 transition hover:border-cyan-200/25 hover:bg-cyan-300/[0.09] disabled:opacity-40"
            >
              {isExecuting ? <Loader2 className="h-3 w-3 animate-spin" /> : <Play className="h-3 w-3" />}
              执行
            </button>
          )}
          {onSettings && (
            <IconButton label="设置" onClick={onSettings}>
              <Settings className="h-3.5 w-3.5" />
            </IconButton>
          )}
          {onHide && (
            <IconButton label="隐藏" onClick={onHide} tone="amber">
              <EyeOff className="h-3.5 w-3.5" />
            </IconButton>
          )}
          {onUninstall && (
            <IconButton label="卸载" onClick={onUninstall} tone="rose">
              <Trash2 className="h-3.5 w-3.5" />
            </IconButton>
          )}
        </div>

        <div className="relative flex min-w-[54px] justify-end">
          <AnimatePresence>
            {showPermissions ? (
              <motion.div
                initial={{ opacity: 0, x: 8 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: 8 }}
                className="flex items-center gap-1.5 text-slate-500"
                title={permissions?.length ? "权限" : "权限占位"}
              >
                {displayPermissions.slice(0, 5).map(({ id, label }) => {
                  const Icon = ICON_BY_ID[id] ?? Shield;
                  return (
                    <span key={id} className="text-slate-500 transition group-hover:text-cyan-100/70" title={label}>
                      <Icon className="h-3.5 w-3.5" />
                    </span>
                  );
                })}
              </motion.div>
            ) : (
              <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="flex items-center gap-1 text-cyan-100/45">
                <Radio className="h-3.5 w-3.5" />
                <span className="font-mono text-[10px]">PERM</span>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>
    </motion.div>
  );
}

function IconButton({
  children,
  label,
  onClick,
  tone = "cyan",
}: {
  children: React.ReactNode;
  label: string;
  onClick: () => void;
  tone?: "cyan" | "amber" | "rose";
}) {
  const toneClass =
    tone === "rose"
      ? "hover:border-rose-300/25 hover:bg-rose-300/[0.08] hover:text-rose-100"
      : tone === "amber"
        ? "hover:border-amber-300/25 hover:bg-amber-300/[0.08] hover:text-amber-100"
        : "hover:border-cyan-200/20 hover:bg-cyan-300/[0.07] hover:text-cyan-50";

  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      className={cn("flex h-7 w-7 items-center justify-center rounded-[7px] border border-cyan-200/[0.07] bg-cyan-300/[0.018] text-slate-500 transition", toneClass)}
      title={label}
    >
      {children}
    </button>
  );
}
