/**
 * Credentials 链路（网页注册、网页登录、L2 邮箱密码校验）共用的「密码明文」取值。
 *
 * - 数据库存的是 **bcrypt(明文)**，不存明文；登录时用**同一套规则**取出表单里的字符串再 `bcrypt.compare`。
 * - **不对密码做 trim**：避免注册与登录一端 trim、一端不 trim 导致同一视觉输入对应不同哈希。
 * - 邮箱在各调用方单独 `trim` + `toLowerCase()`，与密码无关。
 */
export function passwordPlainForCredentials(raw: unknown): string {
  return typeof raw === "string" ? raw : "";
}
