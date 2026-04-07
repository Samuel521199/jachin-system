/** 流式解析：将正文与 <redacted_thinking>...</redacted_thinking> 隔离到不同字段 */

const OPEN = "<redacted_thinking>";
const CLOSE = "</redacted_thinking>";

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
  if (forceReasoning) {
    return { content, reasoning: reasoning + delta };
  }

  let c = content;
  let r = reasoning;
  let s = acc.pending + delta;
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

/**
 * 统一规范化：先 `<redacted_thinking>`，再 ThinkingProcess/Content 类分隔。
 * 用于流式收尾与每条 chunk 合并后的展示字段。
 */
export function normalizeAssistantOutput(text: string): { content: string; reasoning: string } {
  const tag = splitCompleteThinkingText(text);
  let body = tag.content;
  let reasoning = tag.reasoning.trim();

  if (!looksLikeThinkingProcessStyle(body)) {
    return { content: body, reasoning };
  }

  const tp = splitThinkingProcessPattern(body);
  reasoning = [reasoning, tp.reasoning].filter(Boolean).join("\n\n").trim();
  return { content: tp.content, reasoning };
}

/** 在已由 processReasoningDelta 拆开标签后，再套一层 ThinkingProcess/Content 解析 */
export function mergeAssistantParts(
  contentAfterTags: string,
  reasoningFromTags: string,
): { content: string; reasoning: string } {
  const n = normalizeAssistantOutput(contentAfterTags);
  const reasoning = [reasoningFromTags?.trim(), n.reasoning].filter(Boolean).join("\n\n").trim();
  return { content: n.content, reasoning };
}
