/**
 * 消息持久化工具
 * 
 * 使用 localStorage 保存和加载消息历史
 */

/**
 * 可选：标记本条 assistant 气泡为「待交互的工具调用」（生成式 UI）。
 * 缺省则无此字段，行为与历史版本完全一致。
 */
export interface StoredToolCall {
  /** 与后端 function / tool 名对齐，用于 SkillUIRegistry 查找 */
  name: string;
  args: Record<string, unknown>;
  id?: string;
  /** 用户已在自定义面板中提交结果后置 true，之后走普通正文渲染 */
  resolved?: boolean;
}

export interface StoredMessage {
  role: "user" | "assistant" | "system";
  content: string;
  /** 思考过程（与正文隔离；可来自 reasoning_content / redacted_thinking / Sensory step） */
  reasoning?: string;
  timestamp: number;
  /** 回复来源：L3 直连大模型 / L2 兜底 */
  source?: "L3" | "L2";
  /** Structured chat control protocol emitted by L3 for pending confirmation turns. */
  pending_confirmation?: {
    decision_id?: string;
    work_order_id?: string;
    task_type?: string;
    risk_level?: string;
    tool?: string;
    confirm_text?: string;
    cancel_text?: string;
  };
  /** Opt-in：可视化 Skill；未设置时所有现有消息逻辑不变 */
  tool_call?: StoredToolCall;
}

const STORAGE_KEY = "jachin_chat_messages";
const MAX_MESSAGES = 100; // 最多保存 100 条消息

/**
 * 保存消息到 localStorage
 */
export function saveMessages(messages: StoredMessage[]): void {
  try {
    // 只保存最近的 MAX_MESSAGES 条消息
    const messagesToSave = messages.slice(-MAX_MESSAGES);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(messagesToSave));
  } catch (error) {
    console.error("Failed to save messages:", error);
    // 如果存储空间不足，尝试清理旧消息
    if (error instanceof DOMException && error.name === "QuotaExceededError") {
      try {
        // 只保留最近 50 条消息
        const recentMessages = messages.slice(-50);
        localStorage.setItem(STORAGE_KEY, JSON.stringify(recentMessages));
      } catch (retryError) {
        console.error("Failed to save messages after cleanup:", retryError);
      }
    }
  }
}

/**
 * 从 localStorage 加载消息
 */
export function loadMessages(): StoredMessage[] {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (!stored) {
      return [];
    }
    const messages = JSON.parse(stored) as StoredMessage[];
    // 验证消息格式
    return messages.filter(
      (msg) =>
        msg &&
        typeof msg.role === "string" &&
        typeof msg.content === "string" &&
        typeof msg.timestamp === "number"
    );
  } catch (error) {
    console.error("Failed to load messages:", error);
    return [];
  }
}

/**
 * 清空消息历史
 */
export function clearMessages(): void {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch (error) {
    console.error("Failed to clear messages:", error);
  }
}

/**
 * 添加单条消息并保存
 */
export function addMessage(
  messages: StoredMessage[],
  message: StoredMessage
): StoredMessage[] {
  const newMessages = [...messages, message];
  saveMessages(newMessages);
  return newMessages;
}

/**
 * 批量添加消息并保存
 */
export function addMessages(
  messages: StoredMessage[],
  newMessages: StoredMessage[]
): StoredMessage[] {
  const updatedMessages = [...messages, ...newMessages];
  saveMessages(updatedMessages);
  return updatedMessages;
}

function _normalizeToolName(n: string): string {
  return (n || "").replace(/^core:/i, "").trim().toLowerCase();
}

function _toolNamesMatch(stored: string, payload: string): boolean {
  return _normalizeToolName(stored) === _normalizeToolName(payload);
}

/** 从后往前查找带未解决 tool_call 且与本次提交匹配的 assistant 气泡下标 */
export function findUnresolvedToolCallMessageIndex(
  messages: StoredMessage[],
  payload: { toolName: string; toolCallId?: string }
): number {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (m.role !== "assistant" || !m.tool_call || m.tool_call.resolved) continue;
    const tc = m.tool_call;
    if (payload.toolCallId) {
      if (tc.id === payload.toolCallId) return i;
    } else if (_toolNamesMatch(tc.name, payload.toolName)) {
      return i;
    }
  }
  return -1;
}

/** 用户关闭侧栏画布且未提交：将对应 tool_call 标为已解决并写入说明文案 */
export function dismissUnresolvedToolCallMessage(
  messages: StoredMessage[],
  payload: { toolName: string; toolCallId?: string },
  content = "已关闭侧栏画布，未向 L3 提交参数。"
): StoredMessage[] {
  const idx = findUnresolvedToolCallMessageIndex(messages, payload);
  if (idx < 0) return messages;
  const next = [...messages];
  const cur = next[idx];
  next[idx] = {
    ...cur,
    tool_call: { ...cur.tool_call!, resolved: true },
    content,
  };
  return next;
}

/** 将工具 UI 提交结果合并进历史中的对应气泡（从后往前匹配） */
export function resolveToolCallInMessages(
  messages: StoredMessage[],
  payload: { toolName: string; toolCallId?: string; result: unknown }
): StoredMessage[] {
  let idx = -1;
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (m.role !== "assistant" || !m.tool_call || m.tool_call.resolved) continue;
    const tc = m.tool_call;
    if (payload.toolCallId) {
      if (tc.id === payload.toolCallId) {
        idx = i;
        break;
      }
    } else if (_toolNamesMatch(tc.name, payload.toolName)) {
      idx = i;
      break;
    }
  }
  if (idx < 0) return messages;

  const cur = messages[idx];
  const tc = cur.tool_call!;
  let summary: string;
  if (typeof payload.result === "object" && payload.result !== null && "templateId" in payload.result) {
    const r = payload.result as { templateId?: string; label?: string };
    summary = r.label ? `已选择模版：${r.label}` : `已选择模版：${r.templateId ?? ""}`;
  } else if (typeof payload.result === "object" && payload.result !== null && ("style_label" in payload.result || "styleLabel" in payload.result)) {
    const r = payload.result as {
      topic?: string;
      style_id?: string;
      style_label?: string;
      styleLabel?: string;
      word_count_target?: number;
      wordCountTarget?: number;
      audience?: string;
      tone?: string;
      structure?: string;
    };
    const topicShort = (r.topic ?? "").trim().slice(0, 48);
    const style = (r.style_label ?? r.styleLabel ?? "").trim();
    const wc = r.word_count_target ?? r.wordCountTarget ?? "";
    const aud = (r.audience ?? "").trim();
    const tone = (r.tone ?? "").trim();
    const structure = (r.structure ?? "").trim();
    summary = [
      "已确认作文规格",
      topicShort ? `主题「${topicShort}」` : "",
      style ? `文体 ${style}` : "",
      wc !== "" ? `约 ${wc} 字` : "",
      aud ? `读者 ${aud}` : "",
      tone ? `语气 ${tone}` : "",
      structure ? `结构 ${structure}` : "",
    ]
      .filter(Boolean)
      .join(" · ");
    const topicFull = (r.topic ?? "").trim() || "（主题）";
    const toolInput = {
      topic: topicFull,
      style_id: r.style_id,
      style_label: style || undefined,
      word_count_target: typeof wc === "number" ? wc : Number(wc) || 600,
      audience: aud || undefined,
      tone: tone || undefined,
      structure: structure || undefined,
    };
    summary += `\n\n调用 **core:compose_essay** 时可使用 Action Input（JSON）：\n\`\`\`json\n${JSON.stringify(
      {
        topic: toolInput.topic,
        style_id: toolInput.style_id,
        style_label: toolInput.style_label,
        word_count_target: toolInput.word_count_target,
        audience: toolInput.audience,
        tone: toolInput.tone,
        structure: toolInput.structure,
      },
      null,
      2
    )}\n\`\`\``;
  } else {
    try {
      summary = `已提交：${JSON.stringify(payload.result)}`;
    } catch {
      summary = "已提交工具结果";
    }
  }

  const next = [...messages];
  next[idx] = {
    ...cur,
    tool_call: { ...tc, resolved: true },
    content: cur.content?.trim() ? `${cur.content}\n\n${summary}` : summary,
  };
  return next;
}
