-- =============================================================================
-- Jachin Nexus (Layer 1) - Layer 2 实例状态追踪
-- 微内核生态升级：支撑舰队视图、兼容性过滤、心跳判定
-- 详见 docs/MICROKERNEL_ECOSYSTEM_UPGRADE.md
-- =============================================================================

-- 1. 环境类型枚举
CREATE TYPE layer2_environment_type AS ENUM (
  'k8s',
  'docker',
  'bare_metal',
  'raspberry_pi'
);

-- 2. Layer 2 实例表
CREATE TABLE IF NOT EXISTS layer2_instances (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  instance_id TEXT NOT NULL UNIQUE,           -- 实例标识，对应 target_instance_id
  owner_id UUID REFERENCES nexus_users(id),   -- 所属用户
  environment_type layer2_environment_type NOT NULL DEFAULT 'docker',
  core_version TEXT,                         -- 当前微内核版本，如 1.2.0
  active_plugins JSONB DEFAULT '[]',         -- 当前运行中的插件 ID 列表
  last_heartbeat TIMESTAMPTZ,                 -- 最后心跳时间，用于判定在线
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_layer2_instances_instance_id ON layer2_instances(instance_id);
CREATE INDEX idx_layer2_instances_owner ON layer2_instances(owner_id);
CREATE INDEX idx_layer2_instances_last_heartbeat ON layer2_instances(last_heartbeat DESC);

-- 3. 更新触发器
CREATE OR REPLACE FUNCTION update_layer2_instances_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_layer2_instances_updated_at
  BEFORE UPDATE ON layer2_instances
  FOR EACH ROW
  EXECUTE FUNCTION update_layer2_instances_updated_at();
