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
      className="flex h-full min-h-0 w-full min-w-0 flex-col overflow-hidden bg-slate-950/60"
      aria-label="Skill 画布"
    >
      <header className="flex shrink-0 items-start justify-between gap-2 border-b border-white/10 px-3 py-2">
        <div className="min-w-0 flex-1">
          <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-cyan-500/75">Skill 画布</p>
          <p className="truncate font-mono text-xs text-slate-400">{active.toolName}</p>
        </div>
        {onRequestClose != null && (
          <button
            type="button"
            title="关闭画布"
            aria-label="关闭画布"
            onClick={() => onRequestClose()}
            className="shrink-0 rounded-lg border border-white/15 p-1.5 text-slate-400 transition hover:border-white/25 hover:bg-white/10 hover:text-cyan-200"
          >
            <X className="h-4 w-4" aria-hidden />
          </button>
        )}
      </header>
      {/* 红框内滚动：仅本区域 overflow-y-auto，不撑开窗口 */}
      <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden px-3 py-2">
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
