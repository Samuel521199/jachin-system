/** 流式解析：将正文与 <redacted_thinking>...</redacted_thinking> 隔离到不同字段 */

const OPEN = "<redacted_thinking>";
const CLOSE = "</redacted_thinking>";

/**
 * 将各厂商思考标签别名规范为 canonical OPEN/CLOSE，再交给状态机切分。
 */
export function normalizeChunkThinkingTags(chunk: string): string {
  if (!chunk) return chunk;
  let s = chunk;
  s = s.replace(/<\s*redacted_thinking\s*>/gi, OPEN);
  s = s.replace(/<\/\s*redacted_thinking\s*>/gi, CLOSE);
  s = s.replace(/<\s*think\s*>/gi, OPEN);
  s = s.replace(/<\/\s*think\s*>/gi, CLOSE);
  s = s.replace(/<\s*thinking\s*>/gi, OPEN);
  s = s.replace(/<\/\s*thinking\s*>/gi, CLOSE);
  s = s.replace(/<\s*reasoning\s*>/gi, OPEN);
  s = s.replace(/<\/\s*reasoning\s*>/gi, CLOSE);
  return s;
}

/** 展示前剔除仍残留的控制标签字面量 */
export function stripThinkingControlMarkers(text: string): string {
  if (!text) return text;
  return text
    .replace(/<\s*redacted_thinking\s*>/gi, "")
    .replace(/<\/\s*redacted_thinking\s*>/gi, "")
    .replace(/<\s*think\s*>/gi, "")
    .replace(/<\/\s*think\s*>/gi, "")
    .replace(/<\s*thinking\s*>/gi, "")
    .replace(/<\/\s*thinking\s*>/gi, "")
    .replace(/<\s*reasoning\s*>/gi, "")
    .replace(/<\/\s*reasoning\s*>/gi, "");
}

function longestSuffixThatIsPrefixOf(haystack: string, prefix: string): string {
  const max = Math.min(haystack.length, prefix.length - 1);
  for (let len = max; len >= 1; len--) {
    const suf = haystack.slice(-len);
    if (prefix.startsWith(suf)) return suf;
  }
  return "";
}

export type ReasoningStreamAcc = {
  inThinking: boolean;
  pending: string;
};

export function createReasoningStreamAcc(): ReasoningStreamAcc {
  return { inThinking: false, pending: "" };
}

/**
 * 处理一段增量文本；若 forceReasoning 为真则整段计入 reasoning。
 * 返回新的 content / reasoning 完整串，acc 会被就地更新。
 */
export function processReasoningDelta(
  acc: ReasoningStreamAcc,
  content: string,
  reasoning: string,
  delta: string,
  forceReasoning: boolean
): { content: string; reasoning: string } {
  const normalizedDelta = normalizeChunkThinkingTags(delta);

  if (forceReasoning) {
    return {
      content,
      reasoning: reasoning + stripThinkingControlMarkers(normalizedDelta),
    };
  }

  let c = content;
  let r = reasoning;
  let s = acc.pending + normalizedDelta;
  acc.pending = "";

  while (s.length > 0) {
    if (acc.inThinking) {
      const closeIdx = s.indexOf(CLOSE);
      if (closeIdx !== -1) {
        r += s.slice(0, closeIdx);
        s = s.slice(closeIdx + CLOSE.length);
        acc.inThinking = false;
        continue;
      }
      const partialClose = longestSuffixThatIsPrefixOf(s, CLOSE);
      if (partialClose) {
        r += s.slice(0, s.length - partialClose.length);
        acc.pending = partialClose;
        s = "";
      } else {
        r += s;
        s = "";
      }
    } else {
      const openIdx = s.indexOf(OPEN);
      if (openIdx !== -1) {
        c += s.slice(0, openIdx);
        s = s.slice(openIdx + OPEN.length);
        acc.inThinking = true;
        continue;
      }
      const partialOpen = longestSuffixThatIsPrefixOf(s, OPEN);
      if (partialOpen) {
        c += s.slice(0, s.length - partialOpen.length);
        acc.pending = partialOpen;
        s = "";
      } else {
        c += s;
        s = "";
      }
    }
  }

  return { content: c, reasoning: r };
}

/** 对整段字符串一次性拆分（用于最终 answer 校正） */
export function splitCompleteThinkingText(text: string): { content: string; reasoning: string } {
  const acc = createReasoningStreamAcc();
  return processReasoningDelta(acc, "", "", text, false);
}

// ---------------------------------------------------------------------------
// ThinkingProcess / Content 等「伪结构化」模型输出（无 XML 标签时）
// ---------------------------------------------------------------------------

const CONTENT_DELIMS: RegExp[] = [
  /(?:^|\r?\n)\s*Content\s*:\s*/i,
  /(?:^|\r?\n)\s*Final\s*(?:Answer|Response)\s*:\s*/i,
  /(?:^|\r?\n)\s*正式(?:回复|回答)\s*[:：]\s*/,
  /(?:^|\r?\n)\s*答案\s*[:：]\s*/,
];

function findEarliestContentDelimiter(text: string): { index: number; length: number } | null {
  let best: { index: number; length: number } | null = null;
  for (const re of CONTENT_DELIMS) {
    re.lastIndex = 0;
    const m = re.exec(text);
    if (m && (best === null || m.index < best.index)) {
      best = { index: m.index, length: m[0].length };
    }
  }
  return best;
}

/** 从正文里拆出 ThinkingProcess / … / Content: 两段（一次性） */
export function splitThinkingProcessPattern(text: string): { content: string; reasoning: string } {
  const trimmed = text.trim();
  if (!trimmed) return { content: "", reasoning: "" };

  const delim = findEarliestContentDelimiter(trimmed);
  if (delim) {
    const before = trimmed.slice(0, delim.index);
    const after = trimmed.slice(delim.index + delim.length);
    const reasoningBody = before.replace(/^\s*Thinking(?:\s*)Process\s*:\s*\.?\s*/i, "").trim();
    return {
      content: after.trim(),
      reasoning: (reasoningBody || before).trim(),
    };
  }

  if (/^\s*Thinking(?:\s*)Process\s*:/i.test(trimmed)) {
    const reasoning = trimmed.replace(/^\s*Thinking(?:\s*)Process\s*:\s*\.?\s*/i, "").trim();
    return { content: "", reasoning };
  }

  return { content: trimmed, reasoning: "" };
}

function looksLikeThinkingProcessStyle(text: string): boolean {
  return (
    /thinking\s*process\s*:/i.test(text) ||
    /(?:^|\r?\n)\s*content\s*:/i.test(text) ||
    /(?:^|\r?\n)\s*final\s*(?:answer|response)\s*:/i.test(text) ||
    /(?:^|\r?\n)\s*正式(?:回复|回答)\s*[:：]/i.test(text)
  );
}

// ---------------------------------------------------------------------------
// ReAct / 工具痕迹：Thought、Action:、### 思考 等混在正文字段时拆入 reasoning
// ---------------------------------------------------------------------------

function traceLineRe(line: string): boolean {
  return (
    /^\s*(Thought|Thinking)\b/i.test(line) ||
    /^\s*Action\s*:/i.test(line) ||
    /^\s*Action\s+Input\s*:/i.test(line) ||
    /^\s*Observation\s*:/i.test(line) ||
    /^\s*#{1,3}\s*思考\b/.test(line) ||
    /^\s*#{1,3}\s*系统状态\b/.test(line) ||
    /^\s*```/.test(line)
  );
}

/**
 * 检测正文里是否混有 ReAct/工具调用栈，并拆成「思考链 / 对用户回答」。
 * 优先按典型回答开头（抱歉、您好、错误 等）切分；否则按 trace 行前缀消费。
 */
export function splitToolTraceFromContent(text: string): { content: string; reasoning: string } {
  const raw = text.replace(/\r\n/g, "\n");
  const t = raw.trim();
  if (!t || t.length < 20) return { content: text.trim(), reasoning: "" };

  const answerHead =
    /(?:^|[\n]{2,})(抱歉[，,]|您好[，,！!]|好的[，,]|根据您|以下是|综上[，,]|总的来说[，,]|我已经|可以为您|系统(?:错误|提示)|错误[：:]|无法(?:在|创建|访问)|不能创建|经过多次|始终(?:无法|返回)|创建.*时(?:遇到|失败)|I apologize|I'm sorry|Here (?:is|'s)|The (?:file|error)|Error:)/im;

  const hasToolTrace =
    /(?:^|\n)\s*Action\s*:\s*\S+/im.test(t) ||
    /(?:^|\n)\s*Thought[\.…]/im.test(t) ||
    /###\s*思考\b/m.test(t) ||
    /###\s*系统状态\b/m.test(t) ||
    /\bcore:(?:fs|shell)[^\s]*\b/i.test(t) ||
    /Action\s+Input\s*:/i.test(t);

  if (!hasToolTrace) return { content: text.trim(), reasoning: "" };

  const m = answerHead.exec(t);
  if (m && m.index !== undefined && m.index >= 8) {
    let splitAt = m.index;
    const head = m[0];
    const nl = head.match(/^[\n]+/);
    if (nl) splitAt += nl[0].length;
    const reasoningPart = t.slice(0, splitAt).trim();
    const contentPart = t.slice(splitAt).trim();
    if (contentPart.length >= 6 && reasoningPart.length >= 12) {
      return { content: contentPart, reasoning: reasoningPart };
    }
  }

  const lines = t.split("\n");
  let i = 0;
  while (i < lines.length && traceLineRe(lines[i])) {
    i++;
  }
  while (i < lines.length && lines[i].trim() === "") {
    i++;
  }
  while (i < lines.length && /^\s*[\{\[]/.test(lines[i])) {
    i++;
  }

  if (i === 0 || i >= lines.length) return { content: text.trim(), reasoning: "" };

  const reasoning = lines.slice(0, i).join("\n").trim();
  const content = lines.slice(i).join("\n").trim();
  if (content.length < 8) return { content: text.trim(), reasoning: "" };
  return { content, reasoning };
}

/**
 * 统一规范化：先 `<redacted_thinking>`，再 ThinkingProcess/Content，再 ReAct/工具痕迹。
 * 用于流式收尾与每条 chunk 合并后的展示字段。
 */
export function normalizeAssistantOutput(text: string): { content: string; reasoning: string } {
  const tag = splitCompleteThinkingText(text);
  let body = tag.content;
  let reasoning = tag.reasoning.trim();

  if (looksLikeThinkingProcessStyle(body)) {
    const tp = splitThinkingProcessPattern(body);
    reasoning = [reasoning, tp.reasoning].filter(Boolean).join("\n\n").trim();
    body = tp.content;
  }

  const tool = splitToolTraceFromContent(body);
  body = tool.content;
  reasoning = [reasoning, tool.reasoning].filter(Boolean).join("\n\n").trim();

  return {
    content: stripThinkingControlMarkers(body.trim()),
    reasoning: stripThinkingControlMarkers(reasoning),
  };
}

/** 在已由 processReasoningDelta 拆开标签后，再套一层 ThinkingProcess/Content 解析 */
export function mergeAssistantParts(
  contentAfterTags: string,
  reasoningFromTags: string,
): { content: string; reasoning: string } {
  const n = normalizeAssistantOutput(contentAfterTags);
  const reasoning = [reasoningFromTags?.trim(), n.reasoning].filter(Boolean).join("\n\n").trim();
  return {
    content: stripThinkingControlMarkers(n.content),
    reasoning: stripThinkingControlMarkers(reasoning),
  };
}

/**
 * WebSocket 流式归约：单入口更新 assistant 的 content/reasoning（维护 acc 内 inThinking + pending）。
 * `channelIsReasoningMetadata` 为 true 时整段走 reasoning 通道（仍剥离误混入的标签字面量）。
 */
export function applyAssistantStreamChunk(
  acc: ReasoningStreamAcc,
  prevContent: string,
  prevReasoning: string | undefined,
  rawChunk: string,
  channelIsReasoningMetadata: boolean,
): { content: string; reasoning: string } {
  const { content, reasoning } = processReasoningDelta(
    acc,
    prevContent,
    prevReasoning ?? "",
    rawChunk,
    channelIsReasoningMetadata,
  );
  return mergeAssistantParts(content, reasoning);
}
