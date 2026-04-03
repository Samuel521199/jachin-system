/**
 * Jachin Nexus Layer 1 - Drizzle ORM 数据库连接
 * 使用任意 PostgreSQL（本地默认 postgres/postgres）
 */
import { drizzle } from "drizzle-orm/postgres-js";
import postgres from "postgres";
import * as schema from "./schema";

function currentDatabaseUrl(): string {
  return (process.env.DATABASE_URL ?? "").trim();
}

/** 是否已配置数据库（有 DATABASE_URL 即可；每次读取环境变量，避免 .env 更新后仍用旧值） */
export function isDatabaseConfigured(): boolean {
  const s = currentDatabaseUrl();
  return Boolean(s && s.length > 3);
}

let _client: ReturnType<typeof postgres> | null = null;
let _db: Awaited<ReturnType<typeof drizzle>> | null = null;
/** 当前连接池绑定的 URL；与 env 不一致时重建（解决改 .env.local 后仍连旧端口如 15432） */
let _boundUrl = "";

function disposeClient() {
  if (_client) {
    void _client.end({ timeout: 3 }).catch(() => {});
  }
  _client = null;
  _db = null;
  _boundUrl = "";
}

/** 获取 Drizzle 实例，未配置时返回 null */
export function getDb() {
  const url = currentDatabaseUrl();
  if (!url || url.length < 4) {
    disposeClient();
    return null;
  }
  if (_boundUrl !== url) {
    if (_client) {
      void _client.end({ timeout: 3 }).catch(() => {});
    }
    _client = null;
    _db = null;
    _boundUrl = url;
    _client = postgres(url, {
      max: 10,
      idle_timeout: 20,
      connect_timeout: 10,
    });
    _db = drizzle(_client, { schema });
  }
  return _db;
}

/**
 * 从 Drizzle / postgres 抛出的错误链中提取「连不上库」的可读说明（供 API 返回 503）。
 */
export function describeDatabaseConnectError(err: unknown): string | null {
  let cur: unknown = err;
  const seen = new Set<unknown>();
  for (let i = 0; i < 8 && cur != null && !seen.has(cur); i++) {
    seen.add(cur);
    if (cur instanceof Error) {
      const code = (cur as NodeJS.ErrnoException).code;
      if (code === "ECONNREFUSED") {
        return (
          "无法连接 PostgreSQL（连接被拒绝）。请确认数据库已启动，且 DATABASE_URL 的主机、端口正确（直装常见 5432；Docker 映射须与宿主机端口一致）。" +
          " 若刚修改过 cloud/nexus/.env.local，请停止并重新执行 npm run dev，否则进程仍可能使用旧的 DATABASE_URL（例如仍连 15432）。"
        );
      }
      if (code === "ENOTFOUND") {
        return "无法解析 DATABASE_URL 中的数据库主机名，请检查拼写与网络。";
      }
      if (code === "ETIMEDOUT") {
        return "连接数据库超时，请检查 DATABASE_URL、防火墙与数据库是否监听对应地址。";
      }
    }
    cur =
      cur instanceof Error && "cause" in cur && cur.cause !== undefined
        ? cur.cause
        : undefined;
  }
  return null;
}

