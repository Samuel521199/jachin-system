import { config } from "dotenv";

// drizzle-kit 不经过 Next；先 .env 再 .env.local。
// override: true 让文件覆盖**已存在于进程中的**变量（否则终端里残留的 DATABASE_URL=15432 会盖过 .env.local 的 5432）。
config({ path: ".env" });
config({ path: ".env.local", override: true });

import { defineConfig } from "drizzle-kit";

export default defineConfig({
  schema: "./src/db/schema.ts",
  out: "./drizzle",
  dialect: "postgresql",
  dbCredentials: {
    url: process.env.DATABASE_URL ?? "postgres://postgres:postgres@localhost:5432/postgres",
  },
});
