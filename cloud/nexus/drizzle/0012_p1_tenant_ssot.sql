-- P1: Organization = Tenant (SSOT)
-- 1) organizations.is_personal_default
-- 2) 为尚无 organization_users 的用户创建个人默认组织并授予 owner
-- 3) 删除 users.tenant_id（已由 Drizzle schema 移除，数据库需执行 DROP）
-- 依赖 users / organizations / organization_users（由 db:push 创建）；缺表时跳过数据回填。

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'organizations'
  ) THEN
    ALTER TABLE "organizations" ADD COLUMN IF NOT EXISTS "is_personal_default" boolean DEFAULT false NOT NULL;
    UPDATE "organizations" SET "is_personal_default" = false WHERE "is_personal_default" IS NULL;
  END IF;
END $$;

-- 为每个尚未加入任何组织的用户创建个人默认组织（PostgreSQL）
DO $$
DECLARE
  r RECORD;
  new_org_id uuid;
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'users'
  ) AND EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'organizations'
  ) AND EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'organization_users'
  ) THEN
    FOR r IN
      SELECT u.id AS uid FROM users u
      WHERE NOT EXISTS (
        SELECT 1 FROM organization_users ou WHERE ou.user_id = u.id
      )
    LOOP
      new_org_id := gen_random_uuid();
      INSERT INTO organizations (id, name, billing_plan, is_personal_default, created_at, updated_at)
      VALUES (
        new_org_id,
        'Personal workspace',
        'free',
        true,
        now(),
        now()
      );
      INSERT INTO organization_users (id, org_id, user_id, role, created_at)
      VALUES (gen_random_uuid(), new_org_id, r.uid, 'owner', now());
    END LOOP;
  END IF;
END $$;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'users'
  ) THEN
    ALTER TABLE "users" DROP COLUMN IF EXISTS "tenant_id";
  END IF;
END $$;
