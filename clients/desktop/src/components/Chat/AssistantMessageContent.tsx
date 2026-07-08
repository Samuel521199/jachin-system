/**
 * Assistant 气泡正文分发：普通 Markdown/纯文本 vs 生成式 UI（Tool Call 面板）
 *
 * 向后兼容：无 `tool_call` 字段的消息 100% 走原有渲染分支。
 */

import React from "react";
import { CheckCircle2, Loader2, XCircle } from "lucide-react";
import type { StoredMessage } from "../../utils/messageStorage";
import { getAssistantMainBodyForDisplay } from "../../utils/reasoningStreamSplit";
import { MarkdownMessage } from "./MarkdownMessage";
import { getRegisteredSkillUI, getSkillUiRegistration } from "../../skills-ui/skillUIRegistry";
import type { ToolUiSubmitPayload } from "../../skills-ui/types";

export interface AssistantMessageContentProps {
  message: StoredMessage;
  /** 列表中最后一条 assistant：用于流式光标 */
  isLastAssistant: boolean;
  isTyping: boolean;
  /** markdown：ChatUI / ChatPanel / OmniBar；plain：Omni 主壳当前样式 */
  variant: "markdown" | "plain";
  /** ChatUI 流式光标样式 */
  streamingFromWs?: boolean;
  /** 用户完成面板交互后的回调（可 async，用于等待 L3 回包） */
  onToolUiResult?: (payload: ToolUiSubmitPayload) => void | Promise<void>;
  /** 快速回复：用于任务预览确认/取消等轻量控制 */
  onQuickReply?: (text: string) => void;
}

/** 未注册可视化面板的工具：保持「静默执行」体验，仅极轻占位 */
function LegacyToolCallPlaceholder({ toolName }: { toolName: string }) {
  return (
    <div className="flex items-center gap-2 text-xs text-cyan-500/55">
      <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin opacity-70" aria-hidden />
      <span>正在执行工具「{toolName}」…</span>
    </div>
  );
}

/** Canvas 模式：左侧仅提示，真实表单在右侧 SkillCanvasPane */
function CanvasToolCallHintCard({ toolName }: { toolName: string }) {
  return (
    <div className="rounded-lg border border-violet-500/35 bg-violet-950/25 px-3 py-2.5 text-left">
      <p className="text-[11px] font-medium text-violet-200/95">右侧画布</p>
      <p className="mt-1 text-xs leading-relaxed text-violet-100/70">
        正在右侧进行「{toolName}」配置。完成后提交，生成结果将写回本条对话。
      </p>
    </div>
  );
}

function shouldShowMissionConfirmationControls(body: string): boolean {
  const text = body || "";
  const hasConfirmationCue =
    /确认后我再执行|确认执行|确认发送|继续修改|暂不执行|待确认|pending_confirmation|confirm|execute|cancel/i.test(
      text
    );
  const hasMissionCue = /Task Preview:|Lark|发送|发给|任务|执行|打开|整理|计算/i.test(text);
  return hasConfirmationCue && hasMissionCue;
}

function MissionConfirmationControls({ onQuickReply }: { onQuickReply: (text: string) => void }) {
  return (
    <div
      data-chat-interactive
      className="mt-3 flex flex-wrap items-center gap-2 border-t border-cyan-400/15 pt-3"
      onMouseDown={(e) => e.stopPropagation()}
      onPointerDown={(e) => e.stopPropagation()}
    >
      <button
        type="button"
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          onQuickReply("确认执行");
        }}
        className="inline-flex items-center gap-1.5 rounded-md border border-emerald-400/40 bg-emerald-500/15 px-3 py-1.5 text-xs font-medium text-emerald-100 transition hover:border-emerald-300/70 hover:bg-emerald-500/25"
      >
        <CheckCircle2 className="h-3.5 w-3.5" aria-hidden />
        确认执行
      </button>
      <button
        type="button"
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          onQuickReply("取消");
        }}
        className="inline-flex items-center gap-1.5 rounded-md border border-slate-400/25 bg-white/5 px-3 py-1.5 text-xs text-slate-200 transition hover:border-rose-300/55 hover:bg-rose-500/15 hover:text-rose-100"
      >
        <XCircle className="h-3.5 w-3.5" aria-hidden />
        取消
      </button>
    </div>
  );
}

export function AssistantMessageContent({
  message,
  isLastAssistant,
  isTyping,
  variant,
  streamingFromWs = false,
  onToolUiResult,
  onQuickReply,
}: AssistantMessageContentProps) {
  const tc = message.tool_call;

  // ---------- 分支 A：Opt-in 工具调用气泡（未解决前可能拦截正文） ----------
  if (tc && !tc.resolved) {
    const registration = getSkillUiRegistration(tc.name);
    if (registration?.displayMode === "canvas") {
      return <CanvasToolCallHintCard toolName={tc.name} />;
    }
    const Panel = getRegisteredSkillUI(tc.name);
    if (Panel) {
      return (
        <Panel
          toolName={tc.name}
          toolCallId={tc.id}
          args={tc.args ?? {}}
          layout="inline"
          onToolResponse={async (result) => {
            if (onToolUiResult) {
              await Promise.resolve(
                onToolUiResult({ toolName: tc.name, toolCallId: tc.id, result })
              );
            } else {
              console.warn(
                "[SkillUI] onToolUiResult 未注入，无法持久化/回传。请在 Chat 根组件传入回调。",
                { toolName: tc.name, result }
              );
            }
          }}
        />
      );
    }
    // 注册表未命中：传统 Skill，不渲染 Markdown 正文以免与后台状态打架
    return <LegacyToolCallPlaceholder toolName={tc.name} />;
  }

  // ---------- 分支 B：普通 assistant 文本（历史行为） ----------
  /** 主气泡只展示「对用户正文」；调度/Action 等仅在思考链中展示 */
  const body = getAssistantMainBodyForDisplay(message);
  const showMissionControls = !!onQuickReply && shouldShowMissionConfirmationControls(body);
  if (variant === "markdown") {
    return (
      <>
        <MarkdownMessage content={body} />
        {showMissionControls && <MissionConfirmationControls onQuickReply={onQuickReply} />}
        {isLastAssistant && isTyping && (
          <span
            className={
              streamingFromWs
                ? "stream-cursor"
                : "inline-block w-2 h-4 ml-1 bg-cyan-400/80 animate-pulse rounded-sm"
            }
            aria-hidden
          />
        )}
      </>
    );
  }

  return (
    <>
      <div className="break-words whitespace-pre-wrap leading-relaxed font-medium text-cyan-50/95">
        {body}
        {isLastAssistant && isTyping && (
          <span className="ml-0.5 inline-block h-3 w-1.5 animate-pulse bg-cyan-400/90 align-middle" />
        )}
      </div>
      {showMissionControls && <MissionConfirmationControls onQuickReply={onQuickReply} />}
    </>
  );
}
