DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_enum
    WHERE enumlabel = 'MODEL'
      AND enumtypid = 'item_type'::regtype
  ) THEN
    ALTER TYPE item_type ADD VALUE 'MODEL';
  END IF;
END $$;
