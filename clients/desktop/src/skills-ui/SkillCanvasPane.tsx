/**
 * 右侧画布：在宿主提供的「flex-1」槽位内铺满，与左侧聊天列并排；禁止 fixed/absolute 参与主布局。
 */

import React from "react";
import { X } from "lucide-react";
import { getSkillUiRegistration } from "./skillUIRegistry";
import type { ActiveSkillCanvasPayload } from "./canvasState";
import type { ToolUiSubmitPayload } from "./types";

export interface SkillCanvasPaneProps {
  active: ActiveSkillCanvasPayload;
  onToolUiResult?: (payload: ToolUiSubmitPayload) => void | Promise<void>;
  /** 关闭侧栏（未提交 L3）：由宿主收窄窗口并更新消息 */
  onRequestClose?: () => void;
}

export const SkillCanvasPane: React.FC<SkillCanvasPaneProps> = ({ active, onToolUiResult, onRequestClose }) => {
  const reg = getSkillUiRegistration(active.toolName);
  if (!reg || reg.displayMode !== "canvas") return null;
  const Panel = reg.component;

  return (
    <div
      className="relative flex h-full min-h-0 w-full min-w-0 flex-col overflow-hidden bg-cyan-950/[0.08]"
      aria-label="Skill 画布"
    >
      <span
        className="pointer-events-none absolute left-2 top-2 z-[1] h-2.5 w-2.5 border-l-2 border-t-2 border-cyan-400/70 [box-shadow:0_0_10px_rgba(34,211,238,0.2)]"
        aria-hidden
      />
      <span
        className="pointer-events-none absolute right-2 top-2 z-[1] h-2.5 w-2.5 border-r-2 border-t-2 border-cyan-400/70 [box-shadow:0_0_10px_rgba(34,211,238,0.2)]"
        aria-hidden
      />
      <header className="relative z-[2] flex shrink-0 items-start justify-between gap-2 bg-gradient-to-b from-cyan-950/20 to-transparent px-3 py-2">
        <div className="min-w-0 flex-1">
          <p className="text-[10px] font-semibold uppercase tracking-[0.22em] text-cyan-500/80">Skill 画布</p>
          <p className="truncate font-mono text-xs text-slate-400">{active.toolName}</p>
        </div>
        {onRequestClose != null && (
          <button
            type="button"
            title="关闭画布"
            aria-label="关闭画布"
            onClick={() => onRequestClose()}
            className="shrink-0 bg-transparent p-1.5 text-slate-500 opacity-40 transition-[opacity,filter] duration-200 hover:opacity-100 hover:text-cyan-200 hover:drop-shadow-[0_0_8px_rgba(34,211,238,0.8)]"
          >
            <X className="h-4 w-4" aria-hidden />
          </button>
        )}
      </header>
      {/* 红框内滚动：仅本区域 overflow-y-auto，不撑开窗口 */}
      <div className="relative z-[2] min-h-0 flex-1 overflow-y-auto overflow-x-hidden px-3 py-2">
        <Panel
          toolName={active.toolName}
          toolCallId={active.toolCallId}
          args={active.args}
          layout="canvas"
          onToolResponse={async (result) => {
            if (onToolUiResult) {
              await Promise.resolve(
                onToolUiResult({
                  toolName: active.toolName,
                  toolCallId: active.toolCallId,
                  result,
                }),
              );
            }
          }}
        />
      </div>
    </div>
  );
};
