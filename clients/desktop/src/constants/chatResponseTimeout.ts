/** 单次对话等待 L3/L2 首包或完成的客户端上限（毫秒）。默认可覆盖 compaction+ReAct 长链路；可在 .env 设 VITE_CHAT_RESPONSE_TIMEOUT_MS，最小 30000。 */
const raw = Number(import.meta.env.VITE_CHAT_RESPONSE_TIMEOUT_MS);
export const CHAT_RESPONSE_TIMEOUT_MS =
  Number.isFinite(raw) && raw >= 30_000 ? raw : 600_000;
export const CHAT_RESPONSE_TIMEOUT_SEC = Math.round(CHAT_RESPONSE_TIMEOUT_MS / 1000);
