/**
 * Sensory WebSocket 步骤文案：与 useSensoryWebSocket 的 onStep 展示一致。
 * JSON 等机器可读输出不注入「### 回复」，以免与用户约束冲突。
 */
export function formatAssistantStepPayload(stepType: string, content: string): string {
  if (stepType !== "answer") {
    return content ? `${content}\n\n` : "";
  }
  const t = content.trim();
  if (
    (t.startsWith("{") && t.endsWith("}")) ||
    (t.startsWith("[") && t.endsWith("]"))
  ) {
    return t ? `${t}\n\n` : "";
  }
  return `### 回复\n\n${content}\n\n`;
}
