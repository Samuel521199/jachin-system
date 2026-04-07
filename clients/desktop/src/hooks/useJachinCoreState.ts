/**
 * 将 Sensory WebSocket 事件映射为 Jachin Core 视觉状态机
 * 优先级：HITL > 流式 chunk > 自我纠错闪动 > 思考/动作 > 空闲
 */

import { useEffect, useMemo, useState } from "react";
import { useSensoryWebSocket } from "./useSensoryWebSocket";

export type CoreVisualState = "idle" | "thinking" | "self_heal" | "streaming" | "hitl";

export type ToolFlashKind = "terminal" | "database" | null;

export type SensoryHookResult = ReturnType<typeof useSensoryWebSocket>;

export function useJachinCoreState(
  sensory: SensoryHookResult,
  options?: { /** L2 等无 chunk 事件时的流式回复 */ isTyping?: boolean }
) {
  const { lastPayload, streamingContent, hitlPending, connected } = sensory;
  const isTyping = options?.isTyping ?? false;
  const [selfHealFlash, setSelfHealFlash] = useState(false);
  const [toolFlash, setToolFlash] = useState<ToolFlashKind>(null);

  // 动作步：根据 tool 名在中央闪现终端 / 数据库图标
  useEffect(() => {
    if (lastPayload?.step_type !== "action") return;
    const metaName = (lastPayload.metadata?.tool_name ?? lastPayload.tool ?? "").toString().toLowerCase();
    const content = (lastPayload.content ?? "").toLowerCase();
    let next: ToolFlashKind = null;
    if (metaName.includes("shell") || content.includes("shell_exec") || content.includes("shell")) {
      next = "terminal";
    } else if (
      metaName.includes("sql") ||
      metaName.includes("db") ||
      metaName.includes("database") ||
      content.includes("database")
    ) {
      next = "database";
    }
    if (!next) return;
    setToolFlash(next);
    const t = window.setTimeout(() => setToolFlash(null), 520);
    return () => clearTimeout(t);
  }, [lastPayload]);

  // 观察步且含错误：琥珀/红闪动后回到思考态隐喻
  useEffect(() => {
    if (lastPayload?.step_type !== "observation") return;
    const metaErr = lastPayload.metadata?.error;
    const text = lastPayload.content ?? "";
    const hasErr =
      (typeof metaErr === "string" && metaErr.length > 0) ||
      /\berror\b|失败|exception|traceback/i.test(text);
    if (!hasErr) return;
    setSelfHealFlash(true);
    const t = window.setTimeout(() => setSelfHealFlash(false), 720);
    return () => clearTimeout(t);
  }, [lastPayload]);

  const coreState = useMemo((): CoreVisualState => {
    if (hitlPending) return "hitl";
    if (streamingContent && streamingContent.length > 0) return "streaming";
    if (connected && lastPayload && ["thought", "action"].includes(lastPayload.step_type)) {
      return "thinking";
    }
    if (selfHealFlash) return "self_heal";
    if (isTyping) return "streaming";
    if (connected && lastPayload?.step_type === "chunk") return "streaming";
    return "idle";
  }, [hitlPending, streamingContent, selfHealFlash, lastPayload, connected, isTyping]);

  return { coreState, toolFlash, selfHealFlash };
}
