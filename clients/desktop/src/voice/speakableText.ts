/** 陪伴 TTS：去掉 Markdown / emoji，跳过符号密集段（HUD 仍展示原文） */
const RESULT_HINT_RE =
  /(已|已经|完成|成功|失败|结果|最终|搞定|好了|完成了|已为你|我已|无法|没法|失败了|报错|可以了|请重试)/;
const PROCESS_HINT_RE =
  /(首先|接下来|步骤|第[一二三四五六七八九十]+(?:步|点|轮)?|最后|正在(?:执行|处理|分析|检索|搜索|打开|发送)|我会|我将|分析(?:过程|思路)?|思路|计划|流程|执行中|处理中|如下)/;
const LIST_PREFIX_RE = /^(\d+[\.\)]|[-*•]|第[一二三四五六七八九十]+步|步骤[:：]?)/;

export type SpeakableTextPreparation = {
  text: string | null;
  normalizedText: string;
  skipReason?: string;
  matchedRule?: string;
};

function pickResultClause(s: string): string | null {
  const clauses = s
    .split(/[，。；！？]/)
    .map((x) => x.trim())
    .filter(Boolean);
  if (clauses.length === 0) return null;
  let hit: string | null = null;
  for (let i = clauses.length - 1; i >= 0; i -= 1) {
    const clause = clauses[i];
    if (RESULT_HINT_RE.test(clause)) {
      hit = clause;
      break;
    }
  }
  if (!hit) return null;
  return `${hit}。`;
}

function skipped(normalizedText: string, skipReason: string, matchedRule?: string): SpeakableTextPreparation {
  return { text: null, normalizedText, skipReason, matchedRule };
}

export function prepareSentenceForTtsDetailed(raw: string): SpeakableTextPreparation {
  let s = raw
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/`[^`]*`/g, " ")
    .replace(/[#*_~]/g, "")
    .replace(/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/gu, "")
    .replace(/\s+/g, " ")
    .trim();
  if (s.length < 2) return skipped(s, "too_short");

  const letters = (s.match(/[\p{L}\p{N}]/gu) ?? []).length;
  if (letters < 2) return skipped(s, "too_few_letters");

  const noisy = (s.match(/[^\p{L}\p{N}\s，。！？、；：]/gu) ?? []).length;
  if (noisy / s.length > 0.45) return skipped(s, "symbol_dense");

  const hasResultHint = RESULT_HINT_RE.test(s);
  const hasProcessHint = PROCESS_HINT_RE.test(s);

  // 列表步骤、执行过程类语句默认不朗读，避免把过程全念出来。
  if (LIST_PREFIX_RE.test(s) && !hasResultHint) return skipped(s, "task_list_without_result", "LIST_PREFIX_RE");
  if (hasProcessHint && !hasResultHint) return skipped(s, "task_process_without_result", "PROCESS_HINT_RE");

  // 混合语句优先抽取结果子句，尽量“只报结果”。
  if ((hasResultHint && hasProcessHint) || (hasResultHint && s.length > 36)) {
    const concise = pickResultClause(s);
    if (concise) s = concise;
  }

  return { text: s, normalizedText: s };
}

export function prepareSentenceForTts(raw: string): string | null {
  const prepared = prepareSentenceForTtsDetailed(raw);
  return prepared.text;
}
