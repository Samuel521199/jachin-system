export const DEFAULT_WAKE_WORD = "Jachin";

export type WakeWordValidationResult = { ok: true; value: string } | { ok: false; message: string };

/** 与 VOICE_WAKE_ARCHITECTURE §7.4.2 对齐 */
export function validateWakeWord(input: string): WakeWordValidationResult {
  const trimmed = input.trim();
  if (!trimmed) {
    return { ok: false, message: "唤醒句不能为空" };
  }
  if (trimmed.length > 32) {
    return { ok: false, message: "唤醒句不能超过 32 个字符" };
  }
  const letters = (trimmed.match(/[\p{L}\p{N}]/gu) ?? []).length;
  if (letters < 2) {
    return { ok: false, message: "唤醒句太短，至少 2 个有效字符" };
  }
  const latin = (trimmed.match(/[A-Za-z]/g) ?? []).length;
  if (latin >= 3 && latin === letters && trimmed.length < 3) {
    return { ok: false, message: "英文唤醒句至少 3 个字母" };
  }
  if (!/^[\p{L}\p{N}\s，。！？、；：'"“”\-_.]+$/u.test(trimmed)) {
    return { ok: false, message: "唤醒句含不支持的特殊符号" };
  }
  return { ok: true, value: trimmed };
}
