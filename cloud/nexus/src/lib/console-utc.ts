/**
 * L1 控制台输出：统一添加 UTC 时间前缀，便于跨时区排查
 */
function utcPrefix(): string {
  return new Date().toISOString();
}

export function log(...args: unknown[]): void {
  console.log(utcPrefix(), ...args);
}

export function error(...args: unknown[]): void {
  console.error(utcPrefix(), ...args);
}

export function warn(...args: unknown[]): void {
  console.warn(utcPrefix(), ...args);
}
