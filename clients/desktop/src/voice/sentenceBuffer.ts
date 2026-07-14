const HARD_TTS_BREAK_PUNCTUATIONS = /[。！？.!?]/;
const SOFT_TTS_BREAK_PUNCTUATIONS = /[，,、；;：:]/;
const MIN_SOFT_BREAK_CHARS = 90;

export type SentenceSplit = {
  complete: string[];
  remainder: string;
};

export function splitSentences(buffer: string, incoming: string): SentenceSplit {
  const merged = `${buffer}${incoming}`;
  const complete: string[] = [];
  let acc = "";

  for (const ch of merged) {
    acc += ch;
    const trimmedAcc = acc.trim();
    const shouldBreak =
      HARD_TTS_BREAK_PUNCTUATIONS.test(ch) ||
      (SOFT_TTS_BREAK_PUNCTUATIONS.test(ch) && trimmedAcc.length >= MIN_SOFT_BREAK_CHARS);
    if (shouldBreak) {
      const s = trimmedAcc;
      if (s) complete.push(s);
      acc = "";
    }
  }

  return { complete, remainder: acc };
}
