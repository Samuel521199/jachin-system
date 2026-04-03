/**
 * L1 注册（用户生根）。
 *
 * 主路径：`registerUserOnly` — 仅创建 `users`；工作区须在登录后于
 * `/console/workspace` 创建或加入（见 `docs/ARCHITECTURE_L1_WORKSPACE_L2_GATEWAY_L3.md`）。
 */
import type { getDb } from "@/db";
import { users } from "@/db/schema";

export type NexusDb = NonNullable<ReturnType<typeof getDb>>;

/**
 * 注册 API：仅插入 `users`，不创建组织。
 */
export async function registerUserOnly(
  db: NexusDb,
  params: { email: string; passwordHash: string; name?: string }
): Promise<{ userId: string }> {
  const id = crypto.randomUUID();
  await db.insert(users).values({
    id,
    email: params.email,
    name: params.name ?? null,
    passwordHash: params.passwordHash,
  });
  return { userId: id };
}
