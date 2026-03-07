-- 确保 user_licenses 表存在（应对迁移部分失败或 schema 不一致）
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'user_licenses'
  ) THEN
    -- 枚举（若不存在）
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'license_status') THEN
      CREATE TYPE public.license_status AS ENUM ('ACTIVE', 'EXPIRED', 'REVOKED');
    END IF;

    CREATE TABLE public.user_licenses (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
      tenant_id text NOT NULL,
      item_id uuid NOT NULL REFERENCES public.plugins_registry(id) ON DELETE CASCADE,
      status public.license_status NOT NULL DEFAULT 'ACTIVE',
      purchased_at timestamp with time zone NOT NULL DEFAULT now(),
      expires_at timestamp with time zone
    );

    CREATE UNIQUE INDEX user_licenses_tenant_item_unique
      ON public.user_licenses (tenant_id, item_id);
  END IF;
END $$;
