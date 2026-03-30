import NextAuth from "next-auth";
import { authConfig } from "@/auth.config";

/**
 * 仅供给 `middleware.ts`：与 `auth.ts` 使用同一套 `authConfig` 中的 authorized / session 策略，
 * 但不经过含 Drizzle/bcrypt 的 `auth.ts` 模块图，避免 Edge 捆绑 Node 专用依赖。
 */
const { auth } = NextAuth(authConfig);
export { auth };
