-- 工作区短码 slug（可选，全局唯一）；契约仍以 UUID id 为准
ALTER TABLE "organizations" ADD COLUMN IF NOT EXISTS "slug" varchar(64);
CREATE UNIQUE INDEX IF NOT EXISTS "organizations_slug_unique" ON "organizations" ("slug");
