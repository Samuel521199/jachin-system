import type { StoredMessage } from "../utils/messageStorage";
import { getSkillUiRegistration } from "./skillUIRegistry";

/** 当前应在右侧画布挂载的 tool（从消息列表推导，取最近一条未解决的 canvas 工具） */
export interface ActiveSkillCanvasPayload {
  toolName: string;
  toolCallId?: string;
  args: Record<string, unknown>;
}

export function getActiveSkillCanvasFromMessages(messages: StoredMessage[]): ActiveSkillCanvasPayload | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (m.role !== "assistant" || !m.tool_call || m.tool_call.resolved) continue;
    const reg = getSkillUiRegistration(m.tool_call.name);
    if (reg?.displayMode === "canvas") {
      return {
        toolName: m.tool_call.name,
        toolCallId: m.tool_call.id,
        args: m.tool_call.args ?? {},
      };
    }
  }
  return null;
}
