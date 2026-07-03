/**
 * 与 drizzle.config.ts 相同顺序加载 .env / .env.local，先执行 safe_drop_legacy_columns.sql，再 drizzle-kit push。
 *
 * 无本机 psql 时，预检必须用与 drizzle-kit **同一串 DATABASE_URL** 执行（通常为 localhost）。
 * 若改用 Docker 内 psql + host.docker.internal，在 Windows 上可能与 Node 连到的不是同一 Postgres，
 * 会出现：预检 VERIFY OK，但 push 仍对另一套库里的 *_not_null 发 DROP 并 42P16。
 */
import { config } from "dotenv";
import { readFileSync } from "fs";
import { join } from "path";
import { spawnSync } from "child_process";
import postgres from "postgres";

const nexusRoot = process.cwd();

config({ path: join(nexusRoot, ".env") });
config({ path: join(nexusRoot, ".env.local"), override: true });

let dbUrl = process.env.DATABASE_URL;
if (!dbUrl) {
  console.error("[preflight-push] DATABASE_URL missing (.env / .env.local)");
  process.exit(1);
}
if (dbUrl.startsWith("postgres://")) {
  dbUrl = "postgresql://" + dbUrl.slice("postgres://".length);
}

function logTarget(url: string): void {
  try {
    const u = new URL(url);
    const db = u.pathname.replace(/^\//, "") || "(default)";
    console.log(
      `[preflight-push] DATABASE_URL target: host=${u.hostname} port=${u.port || "5432"} database=${db}`,
    );
  } catch {
    console.log("[preflight-push] DATABASE_URL present (could not parse for log)");
  }
}

function hasLocalPsql(): boolean {
  const cmd = process.platform === "win32" ? "where.exe" : "which";
  const r = spawnSync(cmd, ["psql"], {
    encoding: "utf8",
    shell: process.platform === "win32",
    stdio: ["ignore", "pipe", "ignore"],
  });
  return r.status === 0;
}

function runPreflightDocker(body: string): void {
  let dockerUrl = dbUrl!;
  dockerUrl = dockerUrl.replace("@127.0.0.1:", "@host.docker.internal:");
  dockerUrl = dockerUrl.replace("@127.0.0.1/", "@host.docker.internal/");
  dockerUrl = dockerUrl.replace("@localhost:", "@host.docker.internal:");
  dockerUrl = dockerUrl.replace("@localhost/", "@host.docker.internal/");

  console.warn(
    "[preflight-push] Docker fallback: host.docker.internal may differ from Node localhost — install psql or ensure single Postgres listener.",
  );
  logTarget(dockerUrl);

  const r = spawnSync(
    "docker",
    ["run", "--rm", "-i", "postgres:16-alpine", "psql", dockerUrl, "-v", "ON_ERROR_STOP=1", "-f", "-"],
    {
      cwd: nexusRoot,
      input: body,
      stdio: ["pipe", "inherit", "inherit"],
      env: process.env,
    },
  );
  if (r.status !== 0) process.exit(r.status ?? 1);
}

async function runPreflight(): Promise<void> {
  const sqlPath = join(nexusRoot, "scripts", "safe_drop_legacy_columns.sql");
  const body = readFileSync(sqlPath, "utf8");
  logTarget(dbUrl!);

  if (hasLocalPsql()) {
    console.log("[preflight-push] Using local psql (same DATABASE_URL as drizzle-kit).");
    const r = spawnSync("psql", [dbUrl!, "-v", "ON_ERROR_STOP=1", "-f", "-"], {
      cwd: nexusRoot,
      input: body,
      stdio: ["pipe", "inherit", "inherit"],
      env: process.env,
      shell: false,
    });
    if (r.status !== 0) process.exit(r.status ?? 1);
    return;
  }

  console.log(
    "[preflight-push] psql not on PATH; executing SQL via Node `postgres` driver (same DATABASE_URL as drizzle-kit).",
  );
  const client = postgres(dbUrl!, {
    max: 1,
    connect_timeout: 30,
    idle_timeout: 0,
  });
  try {
    await client.unsafe(body);
  } catch (err) {
    console.error("[preflight-push] Node postgres failed:", err);
    await client.end({ timeout: 2 }).catch(() => {});
    runPreflightDocker(body);
    return;
  } finally {
    await client.end({ timeout: 15 }).catch(() => {});
  }
}

/**
 * 与 drizzle-kit 对 public 表的 tableChecks 查询一致（information_schema.CHECK ∩ pg_constraint），
 * 再筛 conname 后缀 _not_null。push 的「删 CHECK」diff 即来源于此；§9 只数 contype=c 时会出现「VERIFY OK 但 push 仍 DROP」。
 */
async function assertDrizzleIntrospectNoLegacyNotNull(url: string, verbose: boolean): Promise<void> {
  const client = postgres(url, {
    max: 1,
    connect_timeout: 30,
    idle_timeout: 0,
  });
  try {
    const rows = await client.unsafe(`
      SELECT tc.table_name, tc.constraint_name
      FROM information_schema.table_constraints AS tc
      JOIN pg_constraint AS con
        ON con.conname = tc.constraint_name
        AND con.conrelid = (
          SELECT c.oid
          FROM pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
          WHERE c.relname = tc.table_name
            AND n.nspname = tc.constraint_schema
        )
      WHERE tc.table_schema = 'public'
        AND tc.constraint_type = 'CHECK'
        AND con.contype = 'c'
        AND trim(tc.constraint_name::text) LIKE '%\\_not_null' ESCAPE '\\'
    `);
    if (rows.length > 0) {
      console.error(
        "[preflight-push] 预检后 drizzle introspect 仍可见下列 *_not_null（与 push 将执行的 DROP 同源），请检查 0.052 或库状态：",
        rows,
      );
      process.exit(1);
    }
    if (verbose) {
      console.log(
        "[preflight-push] Post-preflight: drizzle tableChecks 口径下无 public *_not_null CHECK。",
      );
    }
  } finally {
    await client.end({ timeout: 10 }).catch(() => {});
  }
}

function runDrizzlePush(): void {
  const extra = process.argv.slice(2).filter((a) => a !== "--");
  const args = ["drizzle-kit", "push", ...extra];
  const r = spawnSync("npx", args, {
    cwd: nexusRoot,
    stdio: "inherit",
    env: process.env,
    shell: process.platform === "win32",
  });
  if (r.status !== 0) process.exit(r.status ?? 1);
}

void (async () => {
  const verbose = process.argv.includes("--verbose");
  await runPreflight();
  await assertDrizzleIntrospectNoLegacyNotNull(dbUrl!, verbose);
  runDrizzlePush();
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
