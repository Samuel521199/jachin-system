/**
 * 本地开发用：跳过真实库校验，任意（可空）邮箱密码即可登录。
 * 生产环境（NODE_ENV=production）恒为 false。
 * 开发环境可通过 JACHIN_DOWNLOADS_DEV_LOGIN_BYPASS=0|false|off 显式关闭。
 */
export function isDownloadsDevLoginBypassEnabled(): boolean {
  if (process.env.NODE_ENV === "production") return false;
  const v = process.env.JACHIN_DOWNLOADS_DEV_LOGIN_BYPASS?.trim().toLowerCase();
  if (v === "0" || v === "false" || v === "no" || v === "off") return false;
  return true;
}
