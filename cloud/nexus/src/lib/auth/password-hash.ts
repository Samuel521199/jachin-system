/**
 * 判断 users.password_hash 是否为可用的 bcrypt 串。
 * 注意：PostgreSQL 里 `password_hash IS NOT NULL` 对空字符串 '' 仍为 true，
 * 但空串/短串无法通过 bcrypt.compare，易被误认为「密码没写进库」。
 */
export function credentialsHashUsable(h: string | null | undefined): boolean {
  const s = (h ?? "").trim();
  if (s.length < 59) return false;
  return (
    s.startsWith("$2a$") ||
    s.startsWith("$2b$") ||
    s.startsWith("$2y$")
  );
}
