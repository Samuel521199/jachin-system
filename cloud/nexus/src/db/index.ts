/**
 * Jachin Nexus Layer 1 - Drizzle ORM 数据库连接
 * 去 BaaS 化：完全脱离 Supabase，使用任意 PostgreSQL
 */
import { drizzle } from "drizzle-orm/postgres-js";
import postgres from "postgres";
import * as schema from "./schema";

const connectionString = process.env.DATABASE_URL ?? "";

/** 是否已配置数据库（有 DATABASE_URL 即可，无需 Supabase） */
export function isDatabaseConfigured(): boolean {
  return Boolean(connectionString && connectionString.length > 3);
}

let _client: ReturnType<typeof postgres> | null = null;
let _db: Awaited<ReturnType<typeof drizzle>> | null = null;

/** 获取 Drizzle 实例，未配置时返回 null */
export function getDb() {
  if (!connectionString || connectionString.length < 4) return null;
  if (!_db) {
    _client = postgres(connectionString, {
      max: 10,
      idle_timeout: 20,
      connect_timeout: 10,
    });
    _db = drizzle(_client, { schema });
  }
  return _db;
}

