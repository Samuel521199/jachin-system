/**
 * 消息持久化工具
 * 
 * 使用 localStorage 保存和加载消息历史
 */

export interface StoredMessage {
  role: "user" | "assistant" | "system";
  content: string;
  /** 思考过程（与正文隔离；可来自 reasoning_content / redacted_thinking / Sensory step） */
  reasoning?: string;
  timestamp: number;
  /** 回复来源：L3 直连大模型 / L2 兜底 */
  source?: "L3" | "L2";
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
