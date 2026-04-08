import { drizzle } from "drizzle-orm/postgres-js";
import postgres from "postgres";
import * as schema from "./schema";

function currentDatabaseUrl(): string {
  return (process.env.DATABASE_URL ?? "").trim();
}

export function isDatabaseConfigured(): boolean {
  const s = currentDatabaseUrl();
  return Boolean(s && s.length > 3);
}

let _client: ReturnType<typeof postgres> | null = null;
let _db: ReturnType<typeof drizzle<typeof schema>> | null = null;
let _boundUrl = "";

function disposeClient() {
  if (_client) {
    void _client.end({ timeout: 3 }).catch(() => {});
  }
  _client = null;
  _db = null;
  _boundUrl = "";
}

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

export type AppDb = NonNullable<ReturnType<typeof getDb>>;
