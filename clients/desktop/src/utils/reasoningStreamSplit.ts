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
    /^\s*\[[^\]\n]{1,32}\]\s*\[jachin:heartbeat\]/i.test(line) ||
    /^\s*\[jachin:heartbeat\]/i.test(line) ||
    /^\s*(Thought|Thinking)\b/i.test(line) ||
    /^\s*Action\s*:/i.test(line) ||
    /^\s*Action\s+Input\s*:/i.test(line) ||
    /^\s*Observation\s*:/i.test(line) ||
    /^\s*#{1,3}\s*思考\b/.test(line) ||
    /^\s*#{1,3}\s*系统状态\b/.test(line) ||
    /^\s*```/.test(line) ||
    /^\s*\*{0,2}\s*Drafting\s+the\s+Content\b/i.test(line) ||
    /^\s*\*{0,2}\s*Draft\s*\d+/i.test(line) ||
    /^\s*Critique\s*\d*\s*:/i.test(line) ||
    /^\s*Draft\s*\d+\s*\(\s*InternalMonologue/i.test(line) ||
    /^\s*LogicalInterpretation\s*:/i.test(line) ||
    /^\s*Self\s*[-\u2013]?\s*Correction\b/i.test(line) ||
    /^\s*\*{0,2}\s*Constructing\s+the\s+Response\b/i.test(line)
  );
}

/** 流式早期即可命中：不要求 Action 后必有非空 token */
function hasToolTraceSignal(t: string): boolean {
  return (
    /\[jachin:heartbeat\]/i.test(t) ||
    /(?:^|\n)\s*Action\s*:/im.test(t) ||
    /Action\s+Input\s*:/i.test(t) ||
    /(?:^|\n)\s*Observation\s*:/im.test(t) ||
    /(?:^|\n)\s*Thought\s*:/im.test(t) ||
    /###\s*思考\b/m.test(t) ||
    /###\s*系统状态\b/m.test(t) ||
    /\bcore:[a-z0-9_:]+\b/i.test(t) ||
    /(?:^|\n)\s*\*{0,2}\s*Drafting\s+the\s+Content\b/im.test(t) ||
    /(?:^|\n)\s*\*{0,2}\s*Draft\s*\d+\s*[:(]/im.test(t) ||
    /(?:^|\n)\s*Critique\s*\d*\s*:/im.test(t) ||
    /Draft\s*\d+\s*\(\s*InternalMonologue/im.test(t) ||
    /\bIterative\s+Process\s*\)/i.test(t) ||
    /LogicalInterpretation\s*:/im.test(t) ||
    /Self\s*[-\u2013]?\s*Correction\b/im.test(t) ||
    /Constructing\s+the\s+Response\b/im.test(t)
  );
}

const ANSWER_HEAD_RE =
  /(?:^|[\n]{2,})(抱歉[，,]|您好[，,！!]|好的[，,]|根据您|以下是|综上[，,]|总的来说[，,]|我已经|可以为您|系统(?:错误|提示)|错误[：:]|无法(?:在|创建|访问)|不能创建|经过多次|始终(?:无法|返回)|创建.*时(?:遇到|失败)|I apologize|I'm sorry|Here (?:is|'s)|The (?:file|error)|Error:|Successfully|I've successfully)/im;

/** 对用户可见段落的起点（Final Answer / Disclaimer / 代码块等），取最早匹配 */
const USER_FACING_DELIMS: RegExp[] = [
  /(?:^|\n)\s*(?:Final\s+Answer|Final\s+Response|Content)\s*:\s*/i,
  /(?:^|\n)\s*正式(?:回复|回答)\s*[:：]\s*/,
  /(?:^|\n)\s*(?:Here's\s+(?:my|the)\s+(?:response|answer|reply)|Assistant\s+(?:Reply|Response)|My\s+(?:final\s+)?(?:response|answer))\s*[:：]?\s+/i,
  // 与 Constructing 同行的 **Disclaimer** 也须命中（允许行首 / 冒号后 / 空白后）
  /(?:^|\n|[\s:：])\*{1,2}\s*Disclaimer\b/i,
  /(?:^|\n)\s*\*{1,2}\s*Warning\b(?=[^\n]{0,200}(?:risk|Risk|loss|Loss|backup|Backup|Data|danger))/i,
  /(?:^|\n)\s*\*{1,2}\s*Method\s*\d+/i,
  /(?:^|\n)\s*```(?:powershell|pwsh|bash|shell|cmd)\b/i,
];

function findEarliestUserFacingDelim(tail: string): { index: number; length: number } | null {
  let best: { index: number; length: number } | null = null;
  for (const re of USER_FACING_DELIMS) {
    re.lastIndex = 0;
    const m = re.exec(tail);
    if (m && (best === null || m.index < best.index)) {
      best = { index: m.index, length: m[0].length };
    }
  }
  return best;
}

/** 首个 trace / 内部草稿起点：取最早匹配，含英文 Drafting / Critique / InternalMonologue 迭代块 */
const RE_PARTITION_TRACE_STARTERS: RegExp[] = [
  /(?:^|\n)\s*(?:Action\s*:|Action\s+Input\s*:|Observation\s*:|Thought\s*:)/im,
  /(?:^|\n)\s*#{1,3}\s*思考\b/im,
  /(?:^|\n)\s*#{1,3}\s*系统状态\b/im,
  /(?:^|\n)\s*#{1,3}\s*计划\b/im,
  /(?:^|\n)\s*\(\s*API\s+function\s+calling\s*\)/im,
  /(?:^|\n)\s*\*\*\s*思考\s*\*\*/im,
  /(?:^|\n)\s*\*{0,2}\s*Drafting\s+the\s+Content\b/im,
  /(?:^|\n)\s*\*{0,2}\s*Draft\s*\d+\s*[:(]/im,
  /(?:^|\n)\s*Critique\s*\d*\s*:/im,
  /(?:^|\n)\s*LogicalInterpretation\s*:/im,
  /(?:^|\n)\s*Self\s*[-\u2013]?\s*Correction\b/im,
  /(?:^|\n)\s*\*{0,2}\s*Constructing\s+the\s+Response\b/im,
  // 行中误拼接：**Drafting the Content…（取最早切分点）
  /(?<=[\s\n]|^)(?:\*{1,2}\s*)?Drafting\s+the\s+Content\b/im,
];

function findEarliestTracePartitionIndex(t: string): number | null {
  let best: number | null = null;
  for (const re of RE_PARTITION_TRACE_STARTERS) {
    re.lastIndex = 0;
    const m = re.exec(t);
    if (m && (best === null || m.index < best)) {
      best = m.index;
    }
  }
  return best;
}

/**
 * 从首个 ReAct/工具行起分区：此前为「误写入正文的碎片」一并并入 reasoning；
 * Final Answer: 之后为正文；流式未出现分隔符前，trace 段全部进 reasoning，正文可为空。
 */
export function partitionReactStyleOutput(t: string): { content: string; reasoning: string } | null {
  const cut = findEarliestTracePartitionIndex(t);
  if (cut === null) return null;
  const head = t.slice(0, cut).trim();
  const tail = t.slice(cut).trim();

  const fa = findEarliestUserFacingDelim(tail);
  if (fa) {
    const reasoning = [head, tail.slice(0, fa.index).trim()].filter(Boolean).join("\n\n").trim();
    const content = tail.slice(fa.index + fa.length).trim();
    return { content, reasoning };
  }

  const ah = ANSWER_HEAD_RE.exec(tail);
  if (ah && ah.index !== undefined && ah.index >= 4) {
    let splitAt = ah.index;
    const headMatch = ah[0];
    const nl = headMatch.match(/^[\n]+/);
    if (nl) splitAt += nl[0].length;
    const reasoningPart = [head, tail.slice(0, splitAt).trim()].filter(Boolean).join("\n\n").trim();
    const contentPart = tail.slice(splitAt).trim();
    if (contentPart.length >= 4 && reasoningPart.length >= 1) {
      return { content: contentPart, reasoning: reasoningPart };
    }
  }

  // 单行换行后即自然语言答复：`\n抱歉…` / `\n已成功…` / 英文对用户答复（不要求双换行）
  const nlAnswer =
    /\n\s*(抱歉[，,]|您好[，,！!]|根据您|以下是|我已经|可以为您|系统(?:错误|提示)|错误[：:]|无法(?:在|创建|访问)|已成功|已经(?:成功|完成)|总结[：:]|I (?:cannot|apologize|need to|must|'m sorry|would)|Below (?:is|are)|To answer|Please note|Here(?:'s| is) (?:what|my|the))/im;
  const asn = nlAnswer.exec(tail);
  if (asn && asn.index >= 1) {
    const splitAt = asn.index;
    const reasoningPart = [head, tail.slice(0, splitAt).trim()].filter(Boolean).join("\n\n").trim();
    const contentPart = tail.slice(splitAt).trim();
    if (contentPart.length >= 4) {
      return { content: contentPart, reasoning: reasoningPart };
    }
  }

  // 仅有调度栈、尚未输出 Final Answer / 自然语言答复：全部归入思考链，避免污染主气泡
  const reasoning = [head, tail].filter(Boolean).join("\n\n").trim();
  return { content: "", reasoning };
}

/**
 * 检测正文里是否混有 ReAct/工具调用栈，并拆成「思考链 / 对用户回答」。
 * 优先按典型回答开头（抱歉、您好、错误 等）切分；否则按 trace 行前缀消费。
 */
export function splitToolTraceFromContent(text: string): { content: string; reasoning: string } {
  const raw = text.replace(/\r\n/g, "\n");
  const t = raw.trim();
  if (!t) return { content: "", reasoning: "" };

  const partitioned = partitionReactStyleOutput(t);
  if (partitioned) {
    return partitioned;
  }

  // 无「行首 Action:」类标记时，仍可能含 ### 思考 / core: 等
  if (!hasToolTraceSignal(t) && !/###\s*思考\b/m.test(t)) {
    return { content: text.trim(), reasoning: "" };
  }

  // 短文本：只要已有工具痕迹信号就允许拆分（流式前几字符不再被 20 字门槛挡掉）
  if (t.length < 20 && !hasToolTraceSignal(t)) {
    return { content: text.trim(), reasoning: "" };
  }

  const answerHead = ANSWER_HEAD_RE;

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
  if (content.length < 4 && reasoning.length > 0) {
    return { content: "", reasoning: text.trim() };
  }
  if (content.length < 8) return { content: text.trim(), reasoning: "" };
  return { content, reasoning };
}

/** 匹配 `Final Answer` + 冒号（ASCII/全角），允许词间零宽空白；兼容 `}Final Answer:` 粘连 */
const RE_FINAL_ANSWER_MARKER =
  /Final[\s\u200B-\u200D\uFEFF]*Answer[\s\u200B-\u200D\uFEFF]*[:：]/i;

/**
 * ReAct 全量分割：首个 `Final Answer:` 之前为思考链，之后为对用户正文。
 * 在**完整累加串**上调用即可，不受 WebSocket chunk 切碎 `Thought:` 或 `}Final Answer:` 粘连影响。
 */
export function partitionByFinalAnswerMarker(text: string): {
  reasoning: string;
  content: string;
  hasMarker: boolean;
} {
  const s = text.replace(/\r\n/g, "\n");
  const m = RE_FINAL_ANSWER_MARKER.exec(s);
  if (!m) {
    return { reasoning: s.trimEnd(), content: "", hasMarker: false };
  }
  const end = m.index + m[0].length;
  return {
    reasoning: s.slice(0, m.index).trim(),
    content: s.slice(end).trim(),
    hasMarker: true,
  };
}

/**
 * 流式更新：把上一条 assistant 的 reasoning+content 与新片段拼成全量，再按 Final Answer: 分割。
 * 替代对单个 chunk 做关键词匹配。
 */
export function mergeAssistantFlatAndSplitFinalAnswer(
  last: { reasoning?: string; content?: string },
  appendText: string,
  meta?: { isReasoning?: boolean; reasoningAppend?: string },
): { content: string; reasoning: string } {
  const prevR = last.reasoning ?? "";
  const prevC = last.content ?? "";
  let base = prevR + prevC;
  if (meta?.isReasoning) {
    base += appendText;
  } else {
    const ap = meta?.reasoningAppend?.trim();
    if (ap) base += "\n\n" + ap;
    base += appendText;
  }
  const flat = base.replace(/\r\n/g, "\n");
  const r = partitionByFinalAnswerMarker(flat);
  if (r.hasMarker) {
    return {
      reasoning: stripThinkingControlMarkers(r.reasoning),
      content: stripThinkingControlMarkers(r.content),
    };
  }
  /**
   * 已成功切过后，持久化的 reasoning+content 里**不再出现**字面量 `Final Answer:`（标记只在分界处），
   * 若仍用「全串再匹配」会失败并把已写入的正文整段误判为思考链。此时应把新片段续写到正文。
   */
  const hadFinalizedBody = prevC.trim().length > 0;
  if (hadFinalizedBody) {
    if (meta?.isReasoning) {
      return {
        reasoning: stripThinkingControlMarkers(prevR + appendText),
        content: stripThinkingControlMarkers(prevC),
      };
    }
    let newC = prevC;
    const ap = meta?.reasoningAppend?.trim();
    if (ap) newC += "\n\n" + ap;
    newC += appendText;
    return {
      reasoning: stripThinkingControlMarkers(prevR),
      content: stripThinkingControlMarkers(newC),
    };
  }
  return {
    reasoning: stripThinkingControlMarkers(r.reasoning),
    content: stripThinkingControlMarkers(r.content),
  };
}

/**
 * 对**单通道**累计串切分（例如仅正文通道、不含已写入 `last.reasoning` 的前缀）。
 * 聊天主路径应对 `delta` 使用 `mergeAssistantFlatAndSplitFinalAnswer`，以便与思考通道合并后再按
 * `Final Answer:` 分区；误用本函数会导致「Final Answer 在思考流、续写在正文流」时主文为空。
 */
export function splitAssistantFromMergeCumulative(
  mergedCumulativeText: string,
  reasoningAppend?: string,
): { content: string; reasoning: string } {
  let blob = (mergedCumulativeText ?? "").replace(/\r\n/g, "\n");
  const ap = reasoningAppend?.trim();
  if (ap) blob = blob ? `${blob}\n\n${ap}` : ap;
  const r = partitionByFinalAnswerMarker(blob);
  return {
    reasoning: stripThinkingControlMarkers(r.reasoning),
    content: stripThinkingControlMarkers(r.content),
  };
}

/**
 * 统一规范化：先 `<redacted_thinking>`，再 ThinkingProcess/Content，再 ReAct/工具痕迹。
 * 用于流式收尾与每条 chunk 合并后的展示字段。
 */
export function normalizeAssistantOutput(text: string): { content: string; reasoning: string } {
  const tag = splitCompleteThinkingText(text);
  let body = tag.content;
  let reasoning = tag.reasoning.trim();

  /**
   * 模型漏输出 `</think>` 时，`processReasoningDelta` 会把含 `Final Answer:` 的全文留在 reasoning，
   * content 为空。此处用 Final Answer: 从 reasoning 再拆一次，避免收尾 normalize 后主气泡仍空。
   */
  if (!body.trim() && reasoning.length > 0) {
    const probe = partitionByFinalAnswerMarker(reasoning);
    if (probe.hasMarker) {
      reasoning = probe.reasoning.trim();
      body = probe.content;
    } else if (!reasoning.includes(CLOSE) && reasoning.includes(OPEN)) {
      const stripped = reasoning.replace(/<\s*redacted_thinking\s*>/gi, "").trimStart();
      const probe2 = partitionByFinalAnswerMarker(stripped);
      if (probe2.hasMarker) {
        reasoning = probe2.reasoning.trim();
        body = probe2.content;
      }
    }
  }

  const fa = partitionByFinalAnswerMarker(body);
  if (fa.hasMarker) {
    reasoning = [reasoning, fa.reasoning].filter(Boolean).join("\n\n").trim();
    body = fa.content;
    return {
      content: stripThinkingControlMarkers(body.trim()),
      reasoning: stripThinkingControlMarkers(reasoning),
    };
  }

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

/** 管道对齐的 Markdown 表格（含 GFM）：竖线重复易被误判为「口吃」，不得清空主文、不得 strip 破坏 */
function looksLikeMarkdownPipeTable(t: string): boolean {
  if (!t || t.length < 24) return false;
  const lines = t.split(/\r?\n/).filter((l) => /^\s*[|｜]/.test(l));
  return lines.length >= 3;
}

/** 流式/模型异常：同一片段重复拼接，应从主文移除并仅保留在思考链 */
function stripStreamEchoStutter(text: string): string {
  if (looksLikeMarkdownPipeTable(text)) return text.replace(/\r\n/g, "\n").trim();
  let s = text.replace(/\r\n/g, "\n");
  for (let pass = 0; pass < 12; pass++) {
    const next = s.replace(/([\u4e00-\u9fffA-Za-z0-9，。、；：！？\s]{4,80})(\1){1,4}/u, "$1");
    if (next === s) break;
    s = next;
  }
  return s.trim();
}

/** 是否像「我来我来」「帮您完成帮您完成」类内部草稿泄漏到 content */
export function looksLikeStreamStutterEcho(t: string): boolean {
  if (looksLikeMarkdownPipeTable(t)) return false;
  const s = t.replace(/\s/g, "");
  if (s.length < 18) return false;
  if (/(.{5,40})\1{1,3}/u.test(s)) return true;
  if (/(我来){4,}/.test(t)) return true;
  if (/(帮您完成){3,}/.test(t)) return true;
  if (/(分为几个步骤){2,}/.test(t)) return true;
  if (/(这个任务。){2,}/.test(t)) return true;
  if (/(好的){3,}/.test(t.replace(/\s/g, ""))) return true;
  return false;
}

/** 桌面端注入的 Sensory 心跳行，仅应出现在思考链；若误入主文则剔除 */
export function stripSensoryHeartbeatLines(text: string): string {
  if (!text) return "";
  return text
    .replace(/\r\n/g, "\n")
    .split("\n")
    .filter((line) => {
      const t = line.trim();
      if (!t) return true;
      if (/^\[[^\]\n]{1,32}\]\s*\[jachin:heartbeat\]/i.test(t)) return false;
      if (/^\[jachin:heartbeat\]/i.test(t)) return false;
      return true;
    })
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

/** 仅用于 UI：与持久化字段解耦，保证主气泡 Markdown 不再重复展示已归入思考链的调度文本 */
export function getAssistantMainBodyForDisplay(msg: { content?: string; reasoning?: string }): string {
  const finish = (s: string) => stripSensoryHeartbeatLines(stripThinkingControlMarkers(s).trim());

  const contentOnly = String(msg.content ?? "").replace(/\r\n/g, "\n").trim();
  const storedR = String(msg.reasoning ?? "").replace(/\r\n/g, "\n").trim();
  /** 与收尾 normalize 一致：合并后再切 Final Answer，避免「正文只在 reasoning、content 为空」时主气泡空白 */
  const flatMerged = [storedR, contentOnly].filter(Boolean).join("\n\n").trim();

  /** 优先：在「思考链 + 正文」合并串上直接按 `Final Answer:` 取段（与 L3 ReAct 一致），避免后续启发式误伤表格 */
  if (flatMerged) {
    const fa = partitionByFinalAnswerMarker(flatMerged);
    if (fa.hasMarker && fa.content.trim()) {
      return finish(fa.content);
    }
  }

  let raw = contentOnly;
  if (!raw && flatMerged) {
    raw = normalizeAssistantOutput(flatMerged).content.trim();
  }

  if (!raw) return "";

  // 整段为流式重复/口吃草稿：尝试用合并串再切一次（表格等易被误判）；仍不行则仅在思考链展示
  if (looksLikeStreamStutterEcho(raw)) {
    if (flatMerged && flatMerged.length > raw.length) {
      const alt = normalizeAssistantOutput(flatMerged).content.trim();
      if (alt && !looksLikeStreamStutterEcho(alt)) {
        raw = alt;
      } else {
        return "";
      }
    } else {
      const alt = normalizeAssistantOutput(raw).content.trim();
      if (alt && !looksLikeStreamStutterEcho(alt)) {
        raw = alt;
      } else {
        return "";
      }
    }
  }

  const unstuttered = stripStreamEchoStutter(raw);
  const p0 = partitionReactStyleOutput(unstuttered);
  if (p0 && p0.content.trim().length > 0 && !looksLikeStreamStutterEcho(p0.content)) {
    return finish(p0.content);
  }

  const p = partitionReactStyleOutput(raw);
  if (p) return finish(p.content);
  return finish(normalizeAssistantOutput(raw).content);
}

/** 思考链展示：合并持久化 reasoning + 从 content 再解析出的过程段，并去重 */
export function getAssistantReasoningForDisplay(msg: { content?: string; reasoning?: string }): string {
  const raw = String(msg.content ?? "").replace(/\r\n/g, "\n").trim();
  const stored = String(msg.reasoning ?? "").replace(/\r\n/g, "\n").trim();

  /** content 为空但 reasoning 内含 Final Answer 时：主气泡会从合并串取正文，思考链只展示过程段，避免与表格重复 */
  if (!raw && stored) {
    const n = normalizeAssistantOutput(stored);
    const chain = n.reasoning.trim();
    if (chain) return stripThinkingControlMarkers(chain);
  }

  if (looksLikeStreamStutterEcho(raw)) {
    if (!stored) return stripThinkingControlMarkers(raw);
    if (stored.includes(raw) || raw.includes(stored)) {
      return stripThinkingControlMarkers(raw.length >= stored.length ? raw : stored);
    }
    return stripThinkingControlMarkers(`${stored}\n\n${raw}`).trim();
  }

  const p = partitionReactStyleOutput(raw);
  const n = normalizeAssistantOutput(raw);
  const extracted = (p?.reasoning ?? "").trim() || n.reasoning.trim();
  if (!stored) return stripThinkingControlMarkers(extracted);
  if (!extracted) return stripThinkingControlMarkers(stored);
  if (extracted.includes(stored) || stored.includes(extracted)) {
    return stripThinkingControlMarkers(extracted.length >= stored.length ? extracted : stored);
  }
  return stripThinkingControlMarkers(`${stored}\n\n${extracted}`).trim();
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
