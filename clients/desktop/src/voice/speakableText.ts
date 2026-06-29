/** 陪伴 TTS：去掉 Markdown / emoji，跳过符号密集段（HUD 仍展示原文） */
const RESULT_HINT_RE =
  /(已|已经|完成|成功|失败|结果|最终|搞定|好了|完成了|已为你|我已|无法|没法|失败了|报错|可以了|请重试)/;
const PROCESS_HINT_RE =
  /(首先|接下来|然后|步骤|第[一二三四五六七八九十]|先|再|最后|正在|我会|我将|分析|思路|计划|流程|执行中|处理中|如下)/;
const LIST_PREFIX_RE = /^(\d+[\.\)]|[-*•]|第[一二三四五六七八九十]+步|步骤[:：]?)/;

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

export function prepareSentenceForTts(raw: string): string | null {
  let s = raw
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/`[^`]*`/g, " ")
    .replace(/[#*_~]/g, "")
    .replace(/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/gu, "")
    .replace(/\s+/g, " ")
    .trim();
  if (s.length < 2) return null;

  const letters = (s.match(/[\p{L}\p{N}]/gu) ?? []).length;
  if (letters < 2) return null;

  const noisy = (s.match(/[^\p{L}\p{N}\s，。！？、；：]/gu) ?? []).length;
  if (noisy / s.length > 0.45) return null;

  const hasResultHint = RESULT_HINT_RE.test(s);
  const hasProcessHint = PROCESS_HINT_RE.test(s);

  // 列表步骤、执行过程类语句默认不朗读，避免把过程全念出来。
  if (LIST_PREFIX_RE.test(s) && !hasResultHint) return null;
  if (hasProcessHint && !hasResultHint) return null;

  // 混合语句优先抽取结果子句，尽量“只报结果”。
  if ((hasResultHint && hasProcessHint) || (hasResultHint && s.length > 36)) {
    const concise = pickResultClause(s);
    if (concise) s = concise;
  }

  return s;
}
