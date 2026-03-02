-- P0-4 心跳遥测：为 layer2_instances 添加 metrics 列
ALTER TABLE layer2_instances
ADD COLUMN IF NOT EXISTS metrics JSONB DEFAULT '{}';

COMMENT ON COLUMN layer2_instances.metrics IS '硬件指标：cpu_percent, ram_used_mb, ram_total_mb';
