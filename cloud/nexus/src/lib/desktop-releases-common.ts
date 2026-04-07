/**
 * Tauri updater 平台键：{target}-{arch}，与官方 guest 模板一致。
 * @see https://v2.tauri.app/plugin/updater/
 */
export function tauriPlatformKeyFromParts(target: string, arch: string): string {
  const t = target.trim().toLowerCase();
  let a = arch.trim().toLowerCase();
  if (a === "arm64") a = "aarch64";
  return `${t}-${a}`;
}
