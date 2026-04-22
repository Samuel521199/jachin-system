/**
 * L3 WebSocket 轮次 id：服务端常用 `uuid4()[:8]`，内部日志/部分帧可能带完整 UUID。
 * 严格字符串不等会导致桌面端误判「陈旧 answer」并跳过 cleanup，从而 isLoading 卡死。
 */
export function l3RunIdsSameTurn(a: string | undefined, b: string | undefined): boolean {
  const x = (a ?? "").trim();
  const y = (b ?? "").trim();
  if (!x || !y) return true;
  if (x === y) return true;
  const head = (s: string) => s.replace(/-/g, "").toLowerCase().slice(0, 8);
  return head(x) === head(y);
}
