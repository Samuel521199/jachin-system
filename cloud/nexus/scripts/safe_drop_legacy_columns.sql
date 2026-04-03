-- 在运行 drizzle-kit push 前执行，避免「删 download_url / users.tenant_id」造成数据丢失。
--
-- 重要：必须与 package.json「db:push」使用的 DATABASE_URL 指向**同一** Postgres。
-- 若 .env.local 为 localhost:5432，而你对「别的」容器 exec，会出现 users/organizations 不存在但 push 仍报 tenant_id —— 请用下方 PowerShell 脚本。
--
-- 推荐（与 .env.local 一致）:
--   cd D:\Projects\jachi\jachin-system-main\cloud\nexus
--   .\scripts\run-safe-drop-legacy.ps1
--
-- 仅当确认 DATABASE_URL 就是该容器内 postgres 时再用 docker exec:
--   Get-Content .\scripts\safe_drop_legacy_columns.sql | docker exec -i nexus-postgres-1 psql -U postgres -d postgres
--
-- 排查：预检脚本会 RAISE NOTICE（前缀 [jachin-preflight]）。若看不到，先执行:
--   SET client_min_messages TO notice;

SET client_min_messages TO notice;

-- 0) IAM 已迁出 L1（当前 schema 无 iam_*）。遗留表会导致 drizzle-kit push 在删约束时触发 42P16（与主键 id 相关）。
--    与 drizzle/0008_iam_removal_telemetry_privacy.sql 一致；CASCADE 清掉指向它们的 FK。
DROP TABLE IF EXISTS "iam_role_permissions" CASCADE;
DROP TABLE IF EXISTS "iam_roles" CASCADE;

-- 0.05) 去掉「…_not_null」类 CHECK（与 drizzle introspect 对齐）。不得对「CHECK 所引用列 ∩ 当前主键列」
--      在仍有主键时 DROP，否则会 42P16（字段 x 是一个主键）。此类留到各表专用块先卸主键再删。
--      其余 DROP 失败仅 NOTICE，不中断整段预检。
DO $drop_not_null_checks$
DECLARE
  r RECORD;
  touches_pk boolean;
BEGIN
  RAISE NOTICE '[jachin-preflight] 0.05: drop *_not_null CHECK (skip if touches PK columns)';

  FOR r IN
    SELECT tc.table_schema AS sch, tc.table_name AS rel, tc.constraint_name AS cname
    FROM information_schema.table_constraints tc
    WHERE tc.table_schema = 'public'
      AND tc.constraint_type = 'CHECK'
      AND trim(tc.constraint_name) LIKE '%\_not_null' ESCAPE '\'
  LOOP
    SELECT EXISTS (
      SELECT 1
      FROM information_schema.constraint_column_usage ccu
      JOIN information_schema.key_column_usage kcu
        ON kcu.table_schema = ccu.table_schema
        AND kcu.table_name = ccu.table_name
        AND kcu.column_name = ccu.column_name
      JOIN information_schema.table_constraints pk
        ON pk.constraint_schema = kcu.constraint_schema
        AND pk.constraint_name = kcu.constraint_name
        AND pk.constraint_type = 'PRIMARY KEY'
      WHERE ccu.constraint_schema = r.sch
        AND ccu.constraint_name = r.cname
        AND ccu.table_schema = r.sch
        AND ccu.table_name = r.rel
    ) INTO touches_pk;

    IF touches_pk THEN
      RAISE NOTICE '[jachin-preflight] 0.05 skip %.% (CHECK touches PK column): %', r.sch, r.rel, r.cname;
      CONTINUE;
    END IF;

    BEGIN
      EXECUTE format('ALTER TABLE %I.%I DROP CONSTRAINT IF EXISTS %I', r.sch, r.rel, r.cname);
      RAISE NOTICE '[jachin-preflight] 0.05 dropped %.% : %', r.sch, r.rel, r.cname;
    EXCEPTION
      WHEN OTHERS THEN
        RAISE NOTICE '[jachin-preflight] 0.05 FAILED %.% : % — %', r.sch, r.rel, r.cname, SQLERRM;
    END;
  END LOOP;

  RAISE NOTICE '[jachin-preflight] 0.05 done';
END
$drop_not_null_checks$;

-- 0.25) users.id：schema 为 text；旧库常见 uuid。drizzle-kit push 会先 DROP PK 再改类型，易触发 42P16
--      （字段 id 与主键相关）。在保留外键定义的前提下整体改为 text。
DO $users_id_text$
DECLARE
  r RECORD;
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'id' AND udt_name = 'uuid'
  ) THEN
    RETURN;
  END IF;

  DROP TABLE IF EXISTS _jachin_fk_readd;
  DROP TABLE IF EXISTS _jachin_ref_col;
  CREATE TEMP TABLE _jachin_fk_readd (stmt text);
  CREATE TEMP TABLE _jachin_ref_col (relname text, attname text);

  INSERT INTO _jachin_fk_readd
  SELECT 'ALTER TABLE ' || quote_ident(n.nspname) || '.' || quote_ident(t.relname)
    || ' ADD CONSTRAINT ' || quote_ident(c.conname) || ' ' || pg_get_constraintdef(c.oid, true)
  FROM pg_constraint c
  JOIN pg_class t ON t.oid = c.conrelid
  JOIN pg_namespace n ON n.oid = t.relnamespace
  JOIN pg_class rf ON rf.oid = c.confrelid
  JOIN pg_namespace rfn ON rfn.oid = rf.relnamespace
  WHERE c.contype = 'f'
    AND rfn.nspname = 'public'
    AND rf.relname = 'users';

  INSERT INTO _jachin_ref_col
  SELECT DISTINCT t.relname::text, a.attname::text
  FROM pg_constraint c
  JOIN pg_class t ON t.oid = c.conrelid
  JOIN pg_namespace n ON n.oid = t.relnamespace
  JOIN pg_class rf ON rf.oid = c.confrelid
  JOIN pg_namespace rfn ON rfn.oid = rf.relnamespace
  JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS ck(attnum, ord) ON true
  JOIN LATERAL unnest(c.confkey) WITH ORDINALITY AS fk(attnum, ord2) ON ck.ord = fk.ord2
  JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ck.attnum
  JOIN pg_attribute af ON af.attrelid = c.confrelid AND af.attnum = fk.attnum
  WHERE c.contype = 'f'
    AND rfn.nspname = 'public'
    AND rf.relname = 'users'
    AND af.attname = 'id';

  FOR r IN
    SELECT c.conname, n.nspname AS sch, t.relname AS tbl
    FROM pg_constraint c
    JOIN pg_class t ON t.oid = c.conrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    JOIN pg_class rf ON rf.oid = c.confrelid
    JOIN pg_namespace rfn ON rfn.oid = rf.relnamespace
    WHERE c.contype = 'f'
      AND rfn.nspname = 'public'
      AND rf.relname = 'users'
  LOOP
    EXECUTE format('ALTER TABLE %I.%I DROP CONSTRAINT %I', r.sch, r.tbl, r.conname);
  END LOOP;

  ALTER TABLE public.users ALTER COLUMN id TYPE text USING id::text;

  FOR r IN SELECT * FROM _jachin_ref_col
  LOOP
    IF EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_schema = 'public' AND table_name = r.relname AND column_name = r.attname AND udt_name = 'uuid'
    ) THEN
      EXECUTE format(
        'ALTER TABLE public.%I ALTER COLUMN %I TYPE text USING %I::text',
        r.relname,
        r.attname,
        r.attname
      );
    END IF;
  END LOOP;

  FOR r IN SELECT stmt FROM _jachin_fk_readd
  LOOP
    EXECUTE r.stmt;
  END LOOP;

  DROP TABLE IF EXISTS _jachin_fk_readd;
  DROP TABLE IF EXISTS _jachin_ref_col;
END
$users_id_text$;

-- 0.3) organizations.id：schema 为 uuid；旧库常见 text/varchar。drizzle-kit push 可能在仍为主键时先发
--      ALTER COLUMN id DROP NOT NULL，触发 42P16。先拆 FK、改类型、再挂回。
DO $org_id_uuid$
DECLARE
  r RECORD;
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'organizations' AND column_name = 'id'
      AND udt_name IN ('text', 'varchar', 'bpchar')
  ) THEN
    RETURN;
  END IF;

  DROP TABLE IF EXISTS _jachin_org_fk_readd;
  DROP TABLE IF EXISTS _jachin_org_ref_col;
  CREATE TEMP TABLE _jachin_org_fk_readd (stmt text);
  CREATE TEMP TABLE _jachin_org_ref_col (relname text, attname text);

  INSERT INTO _jachin_org_fk_readd
  SELECT 'ALTER TABLE ' || quote_ident(nsrel.nspname) || '.' || quote_ident(trel.relname)
    || ' ADD CONSTRAINT ' || quote_ident(c.conname) || ' ' || pg_get_constraintdef(c.oid, true)
  FROM pg_constraint c
  JOIN pg_class trel ON trel.oid = c.conrelid
  JOIN pg_namespace nsrel ON nsrel.oid = trel.relnamespace
  JOIN pg_class rref ON rref.oid = c.confrelid
  JOIN pg_namespace nsref ON nsref.oid = rref.relnamespace
  WHERE c.contype = 'f'
    AND nsref.nspname = 'public'
    AND rref.relname = 'organizations';

  INSERT INTO _jachin_org_ref_col
  SELECT DISTINCT trel.relname::text, a.attname::text
  FROM pg_constraint c
  JOIN pg_class trel ON trel.oid = c.conrelid
  JOIN pg_namespace nsrel ON nsrel.oid = trel.relnamespace
  JOIN pg_class rref ON rref.oid = c.confrelid
  JOIN pg_namespace nsref ON nsref.oid = rref.relnamespace
  JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS ck(attnum, ord) ON true
  JOIN LATERAL unnest(c.confkey) WITH ORDINALITY AS fk(attnum, ord2) ON ck.ord = fk.ord2
  JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ck.attnum
  JOIN pg_attribute af ON af.attrelid = c.confrelid AND af.attnum = fk.attnum
  WHERE c.contype = 'f'
    AND nsref.nspname = 'public'
    AND rref.relname = 'organizations'
    AND af.attname = 'id';

  FOR r IN
    SELECT c.conname, nsrel.nspname AS sch, trel.relname AS tbl
    FROM pg_constraint c
    JOIN pg_class trel ON trel.oid = c.conrelid
    JOIN pg_namespace nsrel ON nsrel.oid = trel.relnamespace
    JOIN pg_class rref ON rref.oid = c.confrelid
    JOIN pg_namespace nsref ON nsref.oid = rref.relnamespace
    WHERE c.contype = 'f'
      AND nsref.nspname = 'public'
      AND rref.relname = 'organizations'
  LOOP
    EXECUTE format('ALTER TABLE %I.%I DROP CONSTRAINT %I', r.sch, r.tbl, r.conname);
  END LOOP;

  ALTER TABLE public.organizations ALTER COLUMN id TYPE uuid USING trim(id::text)::uuid;

  FOR r IN SELECT * FROM _jachin_org_ref_col
  LOOP
    IF EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_schema = 'public' AND table_name = r.relname AND column_name = r.attname
        AND udt_name IN ('text', 'varchar', 'bpchar')
    ) THEN
      EXECUTE format(
        'ALTER TABLE public.%I ALTER COLUMN %I TYPE uuid USING trim(%I::text)::uuid',
        r.relname,
        r.attname,
        r.attname
      );
    END IF;
  END LOOP;

  FOR r IN SELECT stmt FROM _jachin_org_fk_readd
  LOOP
    EXECUTE r.stmt;
  END LOOP;

  DROP TABLE IF EXISTS _jachin_org_fk_readd;
  DROP TABLE IF EXISTS _jachin_org_ref_col;
END
$org_id_uuid$;

-- 0.35) organization_users：schema 为单列主键 id + unique(org_id,user_id)。旧 Prisma/手工库常见
--      主键为 (org_id,user_id) 或含 id 的复合主键；push 会对 id 发 DROP NOT NULL 而失败。
DO $org_users_pk$
DECLARE
  con_name text;
  pk_cols name[];
  chk RECORD;
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'organization_users'
  ) THEN
    RETURN;
  END IF;

  SELECT c.conname, array_agg(a.attname ORDER BY u.ord)
  INTO con_name, pk_cols
  FROM pg_constraint c
  JOIN pg_class t ON c.conrelid = t.oid
  JOIN pg_namespace n ON t.relnamespace = n.oid
  JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS u(attnum, ord) ON true
  JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = u.attnum
  WHERE n.nspname = 'public' AND t.relname = 'organization_users' AND c.contype = 'p'
  GROUP BY c.conname;

  IF con_name IS NOT NULL AND pk_cols = ARRAY['id']::name[] THEN
    RETURN;
  END IF;

  IF con_name IS NOT NULL THEN
    EXECUTE format('ALTER TABLE public.organization_users DROP CONSTRAINT %I CASCADE', con_name);
  END IF;

  FOR chk IN
    SELECT c.conname AS cname
    FROM pg_constraint c
    JOIN pg_class t ON c.conrelid = t.oid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    WHERE n.nspname = 'public'
      AND t.relname = 'organization_users'
      AND c.contype = 'c'
      AND trim(c.conname::text) LIKE '%\_not_null' ESCAPE '\'
  LOOP
    BEGIN
      EXECUTE format('ALTER TABLE public.organization_users DROP CONSTRAINT IF EXISTS %I', chk.cname);
      RAISE NOTICE '[jachin-preflight] organization_users: dropped check %', chk.cname;
    EXCEPTION
      WHEN OTHERS THEN
        RAISE NOTICE '[jachin-preflight] organization_users: check % skip: %', chk.cname, SQLERRM;
    END;
  END LOOP;

  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'organization_users' AND column_name = 'id'
  ) THEN
    ALTER TABLE public.organization_users
      ADD COLUMN id uuid DEFAULT gen_random_uuid() NOT NULL;
  ELSE
    UPDATE public.organization_users SET id = gen_random_uuid() WHERE id IS NULL;
    ALTER TABLE public.organization_users
      ALTER COLUMN id SET DEFAULT gen_random_uuid();
    ALTER TABLE public.organization_users
      ALTER COLUMN id SET NOT NULL;
    IF EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_schema = 'public' AND table_name = 'organization_users' AND column_name = 'id'
        AND udt_name IN ('text', 'varchar', 'bpchar')
    ) THEN
      ALTER TABLE public.organization_users
        ALTER COLUMN id TYPE uuid USING trim(id::text)::uuid;
    END IF;
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint c
    JOIN pg_class t ON c.conrelid = t.oid
    JOIN pg_namespace n ON t.relnamespace = n.oid
    WHERE n.nspname = 'public' AND t.relname = 'organization_users' AND c.contype = 'p'
  ) THEN
    ALTER TABLE public.organization_users
      ADD CONSTRAINT organization_users_pkey PRIMARY KEY (id);
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_indexes
    WHERE schemaname = 'public'
      AND tablename = 'organization_users'
      AND indexname = 'organization_users_org_id_user_id_unique'
  ) THEN
    CREATE UNIQUE INDEX IF NOT EXISTS organization_users_org_id_user_id_unique
      ON public.organization_users (org_id, user_id);
  END IF;
END
$org_users_pk$;

-- 0.052) 清理仍留在「主键列」上的 Prisma 式 *_not_null（pg_constraint.contype='c'）。0.05 会跳过它们。
--      注意：§9 VERIFY 只信 pg_catalog；勿单独用 information_schema 计数「是否清干净」会误报。
--      此处表集合 = pg 上 trim(conname) 后缀 _not_null ∪ drizzle-kit tableChecks 同源的 information_schema JOIN（与 push 对齐）。
--      流程：pg_constraint（trim(conname) 后缀 _not_null）与 drizzle tableChecks 同源 information_schema JOIN 的并集 →
--      DROP 主键 CASCADE → 删掉上述 CHECK → ADD CONSTRAINT <原名> PRIMARY KEY ...（保留主键约束名）→ 重放 FK。
DO $strip_pk_not_null_checks$
DECLARE
  trel RECORD;
  chk RECORD;
  r RECORD;
  pk_name text;
  pk_def text;
  incoming_cnt int;
BEGIN
  DROP TABLE IF EXISTS _jachin_incoming_fk;
  CREATE TEMP TABLE _jachin_incoming_fk (stmt text);

  RAISE NOTICE '[jachin-preflight] 0.052: strip Prisma *_not_null CHECK (pg_constraint contype=c only + restore FKs)';

  FOR trel IN
    SELECT DISTINCT ON (tbl_oid) tbl_oid, tname
    FROM (
      SELECT rel.oid AS tbl_oid, rel.relname::text AS tname
      FROM pg_constraint con
      JOIN pg_class rel ON rel.oid = con.conrelid
      JOIN pg_namespace ns ON ns.oid = rel.relnamespace
      WHERE ns.nspname = 'public'
        AND rel.relkind IN ('r', 'p')
        AND con.contype = 'c'
        AND trim(con.conname::text) LIKE '%\_not_null' ESCAPE '\'
      UNION
      SELECT rel.oid AS tbl_oid, rel.relname::text AS tname
      FROM information_schema.table_constraints tc
      JOIN pg_constraint con
        ON con.conname = tc.constraint_name
        AND con.conrelid = (
          SELECT c.oid
          FROM pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
          WHERE c.relname = tc.table_name
            AND n.nspname = tc.constraint_schema
        )
      JOIN pg_class rel ON rel.oid = con.conrelid
      JOIN pg_namespace ns ON ns.oid = rel.relnamespace
      WHERE tc.table_schema = 'public'
        AND tc.constraint_type = 'CHECK'
        AND trim(tc.constraint_name) LIKE '%\_not_null' ESCAPE '\'
        AND ns.nspname = 'public'
        AND rel.relkind IN ('r', 'p')
    ) u
    ORDER BY tbl_oid, tname
  LOOP
    pk_name := NULL;
    pk_def := NULL;
    SELECT c.conname, pg_get_constraintdef(c.oid, true)
    INTO pk_name, pk_def
    FROM pg_constraint c
    WHERE c.conrelid = trel.tbl_oid
      AND c.contype = 'p'
    ORDER BY c.oid
    LIMIT 1;

    IF pk_name IS NULL THEN
      RAISE NOTICE '[jachin-preflight] 0.052 %: *_not_null CHECK in pg_catalog but no PK; DROP checks only', trel.tname;
      -- 与 drizzle-kit tableChecks 同源：information_schema.CHECK ∩ pg_constraint（不按 contype 过滤）。
      -- 仅用 pg 的 contype=c + LIKE 会漏掉仍被 introspect JOIN 出来的行 → push 仍发 DROP 并 42P16。
      FOR chk IN
        SELECT tc.constraint_name::text AS cname
        FROM information_schema.table_constraints tc
        JOIN pg_constraint con
          ON con.conname = tc.constraint_name
          AND con.conrelid = trel.tbl_oid
        WHERE tc.table_schema = 'public'
          AND tc.table_name = trel.tname
          AND tc.constraint_type = 'CHECK'
          AND trim(tc.constraint_name::text) LIKE '%\_not_null' ESCAPE '\'
      LOOP
        BEGIN
          EXECUTE format('ALTER TABLE public.%I DROP CONSTRAINT IF EXISTS %I CASCADE', trel.tname, chk.cname);
          RAISE NOTICE '[jachin-preflight] 0.052 % dropped check % (no PK)', trel.tname, chk.cname;
        EXCEPTION
          WHEN OTHERS THEN
            RAISE NOTICE '[jachin-preflight] 0.052 % check %: %', trel.tname, chk.cname, SQLERRM;
        END;
      END LOOP;
      CONTINUE;
    END IF;

    TRUNCATE _jachin_incoming_fk;

    INSERT INTO _jachin_incoming_fk (stmt)
    SELECT format(
      'ALTER TABLE %I.%I ADD CONSTRAINT %I %s',
      nsrel.nspname,
      child.relname,
      c.conname,
      pg_get_constraintdef(c.oid, true)
    )
    FROM pg_constraint c
    JOIN pg_class child ON child.oid = c.conrelid
    JOIN pg_namespace nsrel ON nsrel.oid = child.relnamespace
    WHERE c.contype = 'f'
      AND c.confrelid = trel.tbl_oid
    ORDER BY c.oid;

    SELECT count(*)::int FROM _jachin_incoming_fk INTO incoming_cnt;

    RAISE NOTICE '[jachin-preflight] 0.052 %: DROP PK %, strip *_not_null, then restore PK + % FK(s)',
      trel.tname, pk_name, incoming_cnt;

    EXECUTE format('ALTER TABLE public.%I DROP CONSTRAINT %I CASCADE', trel.tname, pk_name);

    FOR chk IN
      SELECT tc.constraint_name::text AS cname
      FROM information_schema.table_constraints tc
      JOIN pg_constraint con
        ON con.conname = tc.constraint_name
        AND con.conrelid = trel.tbl_oid
      WHERE tc.table_schema = 'public'
        AND tc.table_name = trel.tname
        AND tc.constraint_type = 'CHECK'
        AND trim(tc.constraint_name::text) LIKE '%\_not_null' ESCAPE '\'
    LOOP
      BEGIN
        EXECUTE format('ALTER TABLE public.%I DROP CONSTRAINT IF EXISTS %I CASCADE', trel.tname, chk.cname);
        RAISE NOTICE '[jachin-preflight] 0.052 % dropped check %', trel.tname, chk.cname;
      EXCEPTION
        WHEN OTHERS THEN
          RAISE NOTICE '[jachin-preflight] 0.052 % check %: %', trel.tname, chk.cname, SQLERRM;
      END;
    END LOOP;

    IF NOT EXISTS (
      SELECT 1 FROM pg_constraint c WHERE c.conrelid = trel.tbl_oid AND c.contype = 'p'
    ) THEN
      EXECUTE format('ALTER TABLE public.%I ADD CONSTRAINT %I %s', trel.tname, pk_name, pk_def);
      RAISE NOTICE '[jachin-preflight] 0.052 % PK restored', trel.tname;
    END IF;

    FOR r IN
      SELECT stmt FROM _jachin_incoming_fk ORDER BY stmt
    LOOP
      BEGIN
        EXECUTE r.stmt;
      EXCEPTION
        WHEN OTHERS THEN
          RAISE NOTICE '[jachin-preflight] 0.052 FK restore FAILED (ref %): % — %', trel.tname, r.stmt, SQLERRM;
      END;
    END LOOP;
  END LOOP;

  DROP TABLE IF EXISTS _jachin_incoming_fk;
  RAISE NOTICE '[jachin-preflight] 0.052 done';
END
$strip_pk_not_null_checks$;

-- 0.5) accounts：强制与 schema 一致。常见坑：库上仍是 accounts_pkey(id)，而 drizzle push 先 DROP 复合主键名再 ADD，
--      若复合主键本不存在则 id 主键未去掉，最后 ADD composite 即 42P16（id 仍在主键语义中）。
--      卸主键后再删 provider/provider_account_id 等 *_not_null CHECK，否则 42P16。
DO $$
DECLARE chk RECORD;
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'accounts'
  ) THEN
    RETURN;
  END IF;

  RAISE NOTICE '[jachin-preflight] accounts: drop PKs, strip *_not_null, drop id, add composite PK';

  ALTER TABLE public.accounts DROP CONSTRAINT IF EXISTS accounts_pkey CASCADE;
  ALTER TABLE public.accounts DROP CONSTRAINT IF EXISTS accounts_provider_provider_account_id_pk CASCADE;

  FOR chk IN
    SELECT c.conname AS cname
    FROM pg_constraint c
    JOIN pg_class t ON c.conrelid = t.oid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    WHERE n.nspname = 'public'
      AND t.relname = 'accounts'
      AND c.contype = 'c'
      AND trim(c.conname::text) LIKE '%\_not_null' ESCAPE '\'
  LOOP
    BEGIN
      EXECUTE format('ALTER TABLE public.accounts DROP CONSTRAINT IF EXISTS %I', chk.cname);
      RAISE NOTICE '[jachin-preflight] accounts: dropped check %', chk.cname;
    EXCEPTION
      WHEN OTHERS THEN
        RAISE NOTICE '[jachin-preflight] accounts: check % skip: %', chk.cname, SQLERRM;
    END;
  END LOOP;

  ALTER TABLE public.accounts DROP COLUMN IF EXISTS id;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint c
    JOIN pg_class t ON c.conrelid = t.oid
    JOIN pg_namespace n ON t.relnamespace = n.oid
    WHERE n.nspname = 'public' AND t.relname = 'accounts' AND c.contype = 'p'
  ) THEN
    ALTER TABLE public.accounts
      ADD CONSTRAINT accounts_provider_provider_account_id_pk
      PRIMARY KEY (provider, provider_account_id);
  END IF;
END $$;

DO $$
DECLARE chk RECORD;
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'sessions'
  ) THEN
    RETURN;
  END IF;

  RAISE NOTICE '[jachin-preflight] sessions: drop PK, strip *_not_null (incl. session_token), drop id, PK(session_token)';

  ALTER TABLE public.sessions DROP CONSTRAINT IF EXISTS sessions_pkey CASCADE;

  FOR chk IN
    SELECT c.conname AS cname
    FROM pg_constraint c
    JOIN pg_class t ON c.conrelid = t.oid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    WHERE n.nspname = 'public'
      AND t.relname = 'sessions'
      AND c.contype = 'c'
      AND trim(c.conname::text) LIKE '%\_not_null' ESCAPE '\'
  LOOP
    BEGIN
      EXECUTE format('ALTER TABLE public.sessions DROP CONSTRAINT IF EXISTS %I', chk.cname);
      RAISE NOTICE '[jachin-preflight] sessions: dropped check %', chk.cname;
    EXCEPTION
      WHEN OTHERS THEN
        RAISE NOTICE '[jachin-preflight] sessions: check % skip: %', chk.cname, SQLERRM;
    END;
  END LOOP;

  ALTER TABLE public.sessions DROP COLUMN IF EXISTS id;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint c
    JOIN pg_class t ON c.conrelid = t.oid
    JOIN pg_namespace n ON t.relnamespace = n.oid
    WHERE n.nspname = 'public' AND t.relname = 'sessions' AND c.contype = 'p'
  ) THEN
    ALTER TABLE public.sessions ADD CONSTRAINT sessions_pkey PRIMARY KEY (session_token);
  END IF;
END $$;

-- verification_tokens：强制 (identifier,token) 复合主键，去掉 id 主键/残留 id 列（与 accounts 同类 drizzle 顺序坑）。
DO $$
DECLARE chk RECORD;
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'verification_tokens'
  ) THEN
    RETURN;
  END IF;

  RAISE NOTICE '[jachin-preflight] verification_tokens: drop PKs, strip *_not_null, drop id, composite PK';

  ALTER TABLE public.verification_tokens DROP CONSTRAINT IF EXISTS verification_tokens_pkey CASCADE;
  ALTER TABLE public.verification_tokens DROP CONSTRAINT IF EXISTS verification_tokens_identifier_token_pk CASCADE;

  FOR chk IN
    SELECT c.conname AS cname
    FROM pg_constraint c
    JOIN pg_class t ON c.conrelid = t.oid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    WHERE n.nspname = 'public'
      AND t.relname = 'verification_tokens'
      AND c.contype = 'c'
      AND trim(c.conname::text) LIKE '%\_not_null' ESCAPE '\'
  LOOP
    BEGIN
      EXECUTE format('ALTER TABLE public.verification_tokens DROP CONSTRAINT IF EXISTS %I', chk.cname);
      RAISE NOTICE '[jachin-preflight] verification_tokens: dropped check %', chk.cname;
    EXCEPTION
      WHEN OTHERS THEN
        RAISE NOTICE '[jachin-preflight] verification_tokens: check % skip: %', chk.cname, SQLERRM;
    END;
  END LOOP;

  ALTER TABLE public.verification_tokens DROP COLUMN IF EXISTS id;

  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint c
    JOIN pg_class t ON c.conrelid = t.oid
    JOIN pg_namespace n ON t.relnamespace = n.oid
    WHERE n.nspname = 'public' AND t.relname = 'verification_tokens' AND c.contype = 'p'
  ) THEN
    ALTER TABLE public.verification_tokens
      ADD CONSTRAINT verification_tokens_identifier_token_pk
      PRIMARY KEY (identifier, token);
  END IF;
END $$;

-- 1) plugins_registry：旧列 download_url → package_url
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'plugins_registry' AND column_name = 'download_url'
  ) THEN
    UPDATE plugins_registry
    SET package_url = COALESCE(NULLIF(trim(package_url), ''), download_url)
    WHERE download_url IS NOT NULL AND download_url <> '';
    ALTER TABLE plugins_registry DROP COLUMN IF EXISTS download_url;
  END IF;
END $$;

-- 2) users.tenant_id：若存在组织三表则做 P1 回填（与 drizzle/0012_p1_tenant_ssot.sql 一致）；最后仅当列存在时 DROP
DO $$
DECLARE
  r RECORD;
  new_org_id uuid;
  has_users boolean;
  has_org boolean;
  has_ou boolean;
BEGIN
  SELECT EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'users'
  ) INTO has_users;
  SELECT EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'organizations'
  ) INTO has_org;
  SELECT EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'organization_users'
  ) INTO has_ou;

  IF has_users AND has_org AND has_ou THEN
    ALTER TABLE organizations ADD COLUMN IF NOT EXISTS is_personal_default boolean DEFAULT false NOT NULL;
    UPDATE organizations SET is_personal_default = false WHERE is_personal_default IS NULL;
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

  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'users' AND column_name = 'tenant_id'
  ) THEN
    ALTER TABLE users DROP COLUMN IF EXISTS tenant_id;
  END IF;
END $$;

-- 9) 自检：仅 pg_constraint contype='c' 且 conname 后缀 _not_null（偏窄）。
--    drizzle-kit tableChecks 用 information_schema.CHECK ∩ pg_constraint 且**不按 contype 过滤**，故「§9 OK 但 push 仍 DROP」
--    时以 0.052 内层（同源 information_schema 列表）与 preflight-then-push.ts 的 drizzle 断言为准。
DO $verify_no_prisma_not_null$
DECLARE
  n int;
  sample text;
BEGIN
  SELECT count(*)::int
  INTO n
  FROM pg_constraint c
  JOIN pg_class r ON r.oid = c.conrelid
  JOIN pg_namespace ns ON ns.oid = r.relnamespace
  WHERE ns.nspname = 'public'
    AND r.relkind IN ('r', 'p')
    AND c.contype = 'c'
    AND trim(c.conname::text) LIKE '%\_not_null' ESCAPE '\';

  IF n = 0 THEN
    RAISE NOTICE '[jachin-preflight] VERIFY OK: no public pg_constraint CHECK (c) named *_not_null.';
    RETURN;
  END IF;

  SELECT string_agg(
    ns.nspname::text || '.' || r.relname::text || ':' || c.conname::text,
    ', ' ORDER BY r.relname::text, c.conname::text
  )
  INTO sample
  FROM pg_constraint c
  JOIN pg_class r ON r.oid = c.conrelid
  JOIN pg_namespace ns ON ns.oid = r.relnamespace
  WHERE ns.nspname = 'public'
    AND r.relkind IN ('r', 'p')
    AND c.contype = 'c'
    AND trim(c.conname::text) LIKE '%\_not_null' ESCAPE '\';

  RAISE EXCEPTION '[jachin-preflight] VERIFY FAILED: % Prisma-style *_not_null CHECK (pg_constraint c) still present. Samples: %. Same DATABASE_URL as drizzle-kit; install local psql if docker/host route differs.',
    n,
    left(coalesce(sample, ''), 800);
END
$verify_no_prisma_not_null$;
