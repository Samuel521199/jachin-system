import { config } from "dotenv";

// 加载 .env.local（Next.js 约定），drizzle-kit 不自动加载
config({ path: ".env.local" });
config({ path: ".env" });

import { defineConfig } from "drizzle-kit";

export default defineConfig({
  schema: "./src/db/schema.ts",
  out: "./drizzle",
  dialect: "postgresql",
  dbCredentials: {
    url: process.env.DATABASE_URL ?? "postgres://postgres:postgres@localhost:5432/postgres",
  },
});
