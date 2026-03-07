-- L1 遥测与结算：telemetry_logs, developer_payouts
CREATE TABLE IF NOT EXISTS "telemetry_logs" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  "tenant_id" text NOT NULL,
  "original_id" text NOT NULL,
  "sub_account_id" text NOT NULL,
  "item_id" text NOT NULL,
  "action_name" text NOT NULL,
  "status" text NOT NULL,
  "latency_ms" numeric(12, 2),
  "timestamp" numeric(12, 4) NOT NULL,
  "created_at" timestamp with time zone DEFAULT now() NOT NULL
);

CREATE TABLE IF NOT EXISTS "developer_payouts" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  "developer_id" text NOT NULL,
  "item_id" text NOT NULL,
  "total_calls" integer DEFAULT 0 NOT NULL,
  "unpaid_amount_cents" integer DEFAULT 0 NOT NULL,
  "paid_amount_cents" integer DEFAULT 0 NOT NULL,
  "last_updated_at" timestamp with time zone DEFAULT now() NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS "developer_payouts_dev_item" ON "developer_payouts" ("developer_id", "item_id");

-- 高效索引：租户审计与用量聚合
CREATE INDEX IF NOT EXISTS "idx_telemetry_logs_tenant_ts" ON "telemetry_logs" ("tenant_id", "timestamp");
CREATE INDEX IF NOT EXISTS "idx_telemetry_logs_tenant_item" ON "telemetry_logs" ("tenant_id", "item_id");
CREATE INDEX IF NOT EXISTS "idx_telemetry_logs_item" ON "telemetry_logs" ("item_id");
