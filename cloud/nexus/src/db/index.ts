/**
 * Jachin Nexus Layer 1 - Drizzle ORM 数据库连接
 * 去 BaaS 化 P0：替换 Supabase 黑盒，建立主权数据中枢
 */
import { drizzle } from "drizzle-orm/postgres-js";
import postgres from "postgres";
import * as schema from "./schema";

const connectionString = process.env.DATABASE_URL ?? "";

// 服务端使用：长连接池（避免 Serverless 冷启动重复建连）
const client = postgres(connectionString, {
  max: 10,
  idle_timeout: 20,
  connect_timeout: 10,
});

export const db = drizzle(client, { schema });
