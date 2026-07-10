import type { StoredMessage } from "../../utils/messageStorage";

export type PendingConfirmationControl = NonNullable<StoredMessage["pending_confirmation"]>;

const PENDING_CONFIRMATION_PROTOCOL_RE =
  /<!--\s*jachin-ui:pending-confirmation\s+({[\s\S]*?})\s*-->/i;

const CONFIRMATION_CUE_RE =
  /\u786e\u8ba4\u540e\u6211\u518d\u6267\u884c|\u786e\u8ba4\u6267\u884c|\u786e\u8ba4\u53d1\u9001|\u7ee7\u7eed\u4fee\u6539|\u6682\u4e0d\u6267\u884c|\u5f85\u786e\u8ba4|pending_confirmation|confirm|execute|cancel/i;
const MISSION_CUE_RE =
  /Task Preview:|Lark|\u53d1\u9001|\u53d1\u7ed9|\u4efb\u52a1|\u6267\u884c|\u6253\u5f00|\u6574\u7406|\u8ba1\u7b97/i;

export function extractPendingConfirmationProtocol(body: string): PendingConfirmationControl | null {
  const match = PENDING_CONFIRMATION_PROTOCOL_RE.exec(body || "");
  if (!match?.[1]) return null;
  try {
    const parsed = JSON.parse(match[1]) as PendingConfirmationControl & { type?: string };
    if ((parsed.type || "pending_confirmation") !== "pending_confirmation") return null;
    return parsed;
  } catch {
    return null;
  }
}

export function stripAssistantUiProtocol(body: string): string {
  return (body || "")
    .replace(PENDING_CONFIRMATION_PROTOCOL_RE, "")
    .replace(/<!--\s*jachin-ui:pending-confirmation[\s\S]*$/i, "")
    .trimEnd();
}

export function shouldShowMissionConfirmationControls(
  body: string,
  protocol: PendingConfirmationControl | null,
): boolean {
  if (protocol) return true;
  const text = body || "";
  return CONFIRMATION_CUE_RE.test(text) && MISSION_CUE_RE.test(text);
}

export function pendingConfirmationQuickReplies(protocol: PendingConfirmationControl | null): {
  confirmText: string;
  cancelText: string;
} {
  return {
    confirmText: protocol?.confirm_text?.trim() || "\u786e\u8ba4\u6267\u884c",
    cancelText: protocol?.cancel_text?.trim() || "\u53d6\u6d88",
  };
}
