/**
 * Assistant 气泡正文分发：普通 Markdown/纯文本 vs 生成式 UI（Tool Call 面板）
 *
 * 向后兼容：无 `tool_call` 字段的消息 100% 走原有渲染分支。
 */

import React from "react";
import { CheckCircle2, Ear, ListChecks, Loader2, Route, SendHorizonal, ShieldCheck, XCircle } from "lucide-react";
import type { StoredMessage } from "../../utils/messageStorage";
import { getAssistantMainBodyForDisplay } from "../../utils/reasoningStreamSplit";
import { MarkdownMessage } from "./MarkdownMessage";
import { getRegisteredSkillUI, getSkillUiRegistration } from "../../skills-ui/skillUIRegistry";
import type { ToolUiSubmitPayload } from "../../skills-ui/types";
import {
  extractPendingConfirmationProtocol,
  pendingConfirmationQuickReplies,
  shouldShowMissionConfirmationControls,
  stripAssistantUiProtocol,
  type PendingConfirmationControl,
} from "./pendingConfirmationProtocol";
import { extractTaskSessionProtocol, stripTaskSessionProtocol, type TaskSessionControl } from "./taskSessionProtocol";
import { extractVoiceRuntimeProtocol, stripVoiceRuntimeProtocol, type VoiceRuntimeControl } from "./voiceRuntimeProtocol";

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

function MissionConfirmationControls({
  protocol,
  onQuickReply,
}: {
  protocol: PendingConfirmationControl | null;
  onQuickReply: (text: string) => void;
}) {
  const { confirmText, cancelText } = pendingConfirmationQuickReplies(protocol);
  const choices = protocol?.choices?.filter((choice) => choice?.label && choice?.value) ?? [];
  const isSlotChoice = protocol?.interaction_kind === "slot_choice";
  const planLines =
    isSlotChoice && protocol?.slot === "recipient"
      ? [
          "识别任务：发送消息，缺少收件人",
          "当前状态：等待你选择联系人",
          "执行路径：选择联系人 -> 打开/切换 Lark -> 搜索联系人 -> 发送消息 -> 校验结果",
          "噪声判断：本轮不是闲聊；选择后会继续同一个任务",
        ]
      : protocol
        ? [
            `识别任务：${protocol.task_type || "系统任务"}`,
            `当前状态：等待${isSlotChoice ? "补充信息" : "确认执行"}`,
            `执行工具：${protocol.tool || "按任务自动选择"}`,
          ]
        : [];
  if (choices.length > 0) {
    return (
      <div
        data-chat-interactive
        className="mt-3 flex flex-col gap-2 border-t border-cyan-400/15 pt-3"
        onMouseDown={(e) => e.stopPropagation()}
        onPointerDown={(e) => e.stopPropagation()}
      >
        {planLines.length > 0 ? (
          <div className="rounded-md border border-cyan-400/20 bg-slate-950/35 px-3 py-2 text-[11px] leading-relaxed text-cyan-50/82">
            <div className="mb-1 flex items-center gap-1.5 font-medium text-cyan-100">
              <ListChecks className="h-3.5 w-3.5 text-cyan-200" aria-hidden />
              <span>任务计划</span>
            </div>
            {planLines.map((line) => (
              <div key={line} className="break-words">
                {line}
              </div>
            ))}
          </div>
        ) : null}
        <div className="flex flex-wrap items-center gap-2">
          {choices.map((choice, idx) => {
            const sendText = (choice.send_text || choice.value || choice.label).trim();
            const shortcut = choice.id?.trim();
            return (
              <button
                key={`${choice.value}-${idx}`}
                type="button"
                onClick={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  if (sendText) onQuickReply(sendText);
                }}
                className="inline-flex max-w-full items-center gap-2 rounded-md border border-cyan-400/35 bg-cyan-500/12 px-3 py-1.5 text-left text-xs font-medium text-cyan-50 transition hover:border-cyan-300/70 hover:bg-cyan-500/22"
                title={choice.description || `选择 ${choice.label}`}
              >
                <SendHorizonal className="h-3.5 w-3.5 shrink-0 text-cyan-200" aria-hidden />
                <span className="min-w-0 truncate">{choice.label}</span>
                {shortcut ? (
                  <span className="shrink-0 rounded border border-cyan-300/25 bg-slate-950/40 px-1.5 py-0.5 font-mono text-[10px] text-cyan-200/80">
                    {shortcut}
                  </span>
                ) : null}
              </button>
            );
          })}
        </div>
        <div className="flex justify-end">
          <button
            type="button"
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              onQuickReply(cancelText);
            }}
            className="inline-flex items-center gap-1.5 rounded-md border border-slate-400/20 bg-white/[0.04] px-2.5 py-1 text-[11px] text-slate-300 transition hover:border-rose-300/55 hover:bg-rose-500/15 hover:text-rose-100"
          >
            <XCircle className="h-3 w-3" aria-hidden />
            取消
          </button>
        </div>
      </div>
    );
  }
  return (
    <div
      data-chat-interactive
      className="mt-3 flex flex-col gap-2 border-t border-cyan-400/15 pt-3"
      onMouseDown={(e) => e.stopPropagation()}
      onPointerDown={(e) => e.stopPropagation()}
    >
      {planLines.length > 0 ? (
        <div className="rounded-md border border-cyan-400/20 bg-slate-950/35 px-3 py-2 text-[11px] leading-relaxed text-cyan-50/82">
          <div className="mb-1 flex items-center gap-1.5 font-medium text-cyan-100">
            <ListChecks className="h-3.5 w-3.5 text-cyan-200" aria-hidden />
            <span>任务计划</span>
          </div>
          {planLines.map((line) => (
            <div key={line} className="break-words">
              {line}
            </div>
          ))}
        </div>
      ) : null}
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            onQuickReply(confirmText);
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
            onQuickReply(cancelText);
          }}
          className="inline-flex items-center gap-1.5 rounded-md border border-slate-400/25 bg-white/5 px-3 py-1.5 text-xs text-slate-200 transition hover:border-rose-300/55 hover:bg-rose-500/15 hover:text-rose-100"
        >
          <XCircle className="h-3.5 w-3.5" aria-hidden />
          取消
        </button>
      </div>
    </div>
  );
}

function TaskSessionPanel({ session }: { session: TaskSessionControl | null }) {
  if (!session) return null;
  const steps = session.steps?.filter((step) => step?.label).slice(0, 6) ?? [];
  const basis = session.decision_basis?.filter(Boolean).slice(0, 4) ?? [];
  const status = (session.status || "").toString();
  const statusLabel =
    status === "done"
      ? "已完成"
      : status === "failed"
        ? "未通过"
        : status === "waiting_user"
          ? "等待你"
          : status === "dropped"
            ? "已忽略"
            : status === "running"
              ? "执行中"
              : status || "处理中";
  const statusClass =
    status === "done"
      ? "border-emerald-400/30 text-emerald-100"
      : status === "failed"
        ? "border-rose-400/35 text-rose-100"
        : status === "waiting_user"
          ? "border-amber-300/35 text-amber-100"
          : "border-cyan-400/30 text-cyan-100";

  return (
    <div className="mt-3 rounded-md border border-cyan-400/18 bg-slate-950/35 px-3 py-2.5 text-left text-[11px] leading-relaxed text-cyan-50/82">
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-1.5 font-medium text-cyan-100">
          <Route className="h-3.5 w-3.5 shrink-0 text-cyan-200" aria-hidden />
          <span className="truncate">{session.title || "任务链路"}</span>
        </div>
        <span className={`shrink-0 rounded border px-1.5 py-0.5 ${statusClass}`}>{statusLabel}</span>
      </div>
      {session.current_step ? (
        <div className="mb-2 break-words text-cyan-50/90">当前：{session.current_step}</div>
      ) : null}
      {steps.length > 0 ? (
        <div className="space-y-1">
          {steps.map((step, idx) => (
            <div key={`${step.label}-${idx}`} className="grid grid-cols-[76px_1fr] gap-2">
              <span className="text-cyan-200/75">{step.status || "pending"}</span>
              <span className="min-w-0 break-words">
                {step.label}
                {step.detail ? <span className="text-slate-400">：{step.detail}</span> : null}
              </span>
            </div>
          ))}
        </div>
      ) : null}
      {basis.length > 0 ? (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {basis.map((item) => (
            <span key={item} className="rounded border border-cyan-400/15 bg-cyan-400/10 px-1.5 py-0.5 text-[10px] text-cyan-100/75">
              {item}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function VoiceRuntimePanel({ runtime }: { runtime: VoiceRuntimeControl | null }) {
  if (!runtime) return null;
  const status = String(runtime.status || runtime.decision || "").toLowerCase();
  const statusLabel =
    status === "allow"
      ? "已放行"
      : status === "drop"
        ? "已忽略"
        : status === "confirm"
          ? "需确认"
          : status === "wait"
            ? "等待补充"
            : status === "running"
              ? "执行中"
              : status === "done"
                ? "已完成"
                : status === "failed"
                  ? "未通过"
                  : runtime.status || "语音判断";
  const statusClass =
    status === "allow" || status === "done"
      ? "border-emerald-400/30 text-emerald-100"
      : status === "drop"
        ? "border-slate-400/25 text-slate-200"
        : status === "confirm" || status === "wait"
          ? "border-amber-300/35 text-amber-100"
          : status === "failed"
            ? "border-rose-400/35 text-rose-100"
            : "border-cyan-400/30 text-cyan-100";
  const stages = runtime.stages?.filter((stage) => stage?.label).slice(0, 5) ?? [];
  const corrections = runtime.corrections?.filter((item) => item?.from || item?.to).slice(0, 4) ?? [];
  const raw = String(runtime.raw_text || "").trim();
  const normalized = String(runtime.normalized_text || "").trim();
  return (
    <div className="mt-3 rounded-md border border-sky-300/18 bg-slate-950/40 px-3 py-2.5 text-left text-[11px] leading-relaxed text-sky-50/84">
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-1.5 font-medium text-sky-100">
          <Ear className="h-3.5 w-3.5 shrink-0 text-sky-200" aria-hidden />
          <span className="truncate">语音运行态</span>
        </div>
        <span className={`shrink-0 rounded border px-1.5 py-0.5 ${statusClass}`}>{statusLabel}</span>
      </div>
      <div className="grid gap-1.5">
        {raw ? (
          <div className="grid grid-cols-[4.5rem_1fr] gap-2">
            <span className="text-sky-200/70">我听到</span>
            <span className="min-w-0 break-words text-sky-50/92">{raw}</span>
          </div>
        ) : null}
        {normalized && normalized !== raw ? (
          <div className="grid grid-cols-[4.5rem_1fr] gap-2">
            <span className="text-sky-200/70">理解为</span>
            <span className="min-w-0 break-words text-emerald-100/92">{normalized}</span>
          </div>
        ) : null}
        {runtime.reason_code ? (
          <div className="grid grid-cols-[4.5rem_1fr] gap-2">
            <span className="text-sky-200/70">判断依据</span>
            <span className="min-w-0 break-words">{runtime.reason_code}</span>
          </div>
        ) : null}
        {runtime.current_task || runtime.pending_task ? (
          <div className="grid grid-cols-[4.5rem_1fr] gap-2">
            <span className="text-sky-200/70">任务态</span>
            <span className="min-w-0 break-words">{runtime.current_task || runtime.pending_task}</span>
          </div>
        ) : null}
        {typeof runtime.confidence === "number" ? (
          <div className="grid grid-cols-[4.5rem_1fr] gap-2">
            <span className="text-sky-200/70">置信度</span>
            <span>{Math.round(runtime.confidence * 100)}%</span>
          </div>
        ) : null}
      </div>
      {corrections.length > 0 ? (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {corrections.map((item, idx) => (
            <span key={`${item.from}-${item.to}-${idx}`} className="rounded border border-emerald-300/18 bg-emerald-300/10 px-1.5 py-0.5 text-[10px] text-emerald-100/80">
              {item.from || "?"} {"->"} {item.to || "?"}
            </span>
          ))}
        </div>
      ) : null}
      {stages.length > 0 ? (
        <div className="mt-2 space-y-1 border-t border-sky-300/12 pt-2">
          {stages.map((stage, idx) => (
            <div key={`${stage.label}-${idx}`} className="grid grid-cols-[76px_1fr] gap-2">
              <span className="flex items-center gap-1 text-sky-200/75">
                <ShieldCheck className="h-3 w-3" aria-hidden />
                {stage.status || "ok"}
              </span>
              <span className="min-w-0 break-words">
                {stage.label}
                {stage.detail ? <span className="text-slate-400">：{stage.detail}</span> : null}
              </span>
            </div>
          ))}
        </div>
      ) : null}
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
  const rawBody = getAssistantMainBodyForDisplay(message);
  const protocol = message.pending_confirmation ?? extractPendingConfirmationProtocol(rawBody);
  const taskSession = message.task_session ?? extractTaskSessionProtocol(rawBody);
  const voiceRuntime = message.voice_runtime ?? extractVoiceRuntimeProtocol(rawBody);
  const body = stripVoiceRuntimeProtocol(stripTaskSessionProtocol(stripAssistantUiProtocol(rawBody)));
  const showMissionControls = !!onQuickReply && shouldShowMissionConfirmationControls(body, protocol);
  if (variant === "markdown") {
    return (
      <>
        <MarkdownMessage content={body} />
        <VoiceRuntimePanel runtime={voiceRuntime} />
        <TaskSessionPanel session={taskSession} />
        {showMissionControls && <MissionConfirmationControls protocol={protocol} onQuickReply={onQuickReply} />}
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
      <TaskSessionPanel session={taskSession} />
      <VoiceRuntimePanel runtime={voiceRuntime} />
      {showMissionControls && <MissionConfirmationControls protocol={protocol} onQuickReply={onQuickReply} />}
    </>
  );
}
