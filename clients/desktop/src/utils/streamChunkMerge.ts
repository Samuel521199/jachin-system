/**
 * 合并流式片段：兼容后端发送「增量 delta」与「自开头累加的全量 cumulative」两种形态。
 * 若误将全量当增量拼接，会出现「用户用户用户说」式结巴。
 */
export function mergeStreamChunk(prev: string, incoming: string): { next: string; delta: string } {
  if (incoming === prev) {
    return { next: prev, delta: "" };
  }
  if (incoming.startsWith(prev)) {
    return { next: incoming, delta: incoming.slice(prev.length) };
  }
  if (prev.length > 0 && prev.startsWith(incoming)) {
    return { next: prev, delta: "" };
  }
  return { next: prev + incoming, delta: incoming };
}
