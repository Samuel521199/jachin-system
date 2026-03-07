-- plugins_registry 补齐 version 与 updated_at（与 Drizzle schema 对齐）
ALTER TABLE "public"."plugins_registry" ADD COLUMN IF NOT EXISTS "version" TEXT NOT NULL DEFAULT '1.0.0';
ALTER TABLE "public"."plugins_registry" ADD COLUMN IF NOT EXISTS "updated_at" TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now();
