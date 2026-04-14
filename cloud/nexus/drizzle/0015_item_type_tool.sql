-- 商城 item_type 扩展：原子工具包（TOOL），与四大原语「Tools」上架一致
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_enum e
    JOIN pg_type t ON e.enumtypid = t.oid
    WHERE t.typname = 'item_type' AND e.enumlabel = 'TOOL'
  ) THEN
    ALTER TYPE item_type ADD VALUE 'TOOL';
  END IF;
END $$;
