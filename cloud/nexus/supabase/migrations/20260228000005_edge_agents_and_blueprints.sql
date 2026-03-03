-- =============================================================================
-- Jachin Nexus (Layer 1) - P1-4 核心业务表
-- edge_agents: 边缘智能体设备表（配对、心跳、蓝图绑定）
-- blueprints: 蓝图资产表（Forge 画布 AST 持久化）
-- =============================================================================

-- 1. 边缘智能体状态枚举
CREATE TYPE edge_agent_status AS ENUM ('pending', 'active', 'offline');

-- 2. 蓝图资产表（需先创建，edge_agents 的 current_blueprint_id 引用此表）
CREATE TABLE IF NOT EXISTS blueprints (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  creator_id UUID REFERENCES auth.users(id) ON DELETE SET NULL,
  name TEXT NOT NULL,
  description TEXT,
  ast_json JSONB NOT NULL DEFAULT '{}',
  price NUMERIC(12, 4) DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_blueprints_creator_id ON blueprints(creator_id);
CREATE INDEX idx_blueprints_created_at ON blueprints(created_at DESC);

-- 3. 边缘智能体设备表
CREATE TABLE IF NOT EXISTS edge_agents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES auth.users(id) ON DELETE SET NULL,
  name TEXT,
  pairing_code VARCHAR(6) NOT NULL UNIQUE,
  status edge_agent_status NOT NULL DEFAULT 'pending',
  current_blueprint_id UUID REFERENCES blueprints(id) ON DELETE SET NULL,
  auth_token TEXT,
  pairing_expires_at TIMESTAMPTZ,
  last_heartbeat TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_edge_agents_pairing_code ON edge_agents(pairing_code);
CREATE INDEX idx_edge_agents_user_id ON edge_agents(user_id);
CREATE INDEX idx_edge_agents_status ON edge_agents(status);
CREATE INDEX idx_edge_agents_last_heartbeat ON edge_agents(last_heartbeat DESC);

-- 4. 更新触发器
CREATE OR REPLACE FUNCTION update_edge_agents_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_edge_agents_updated_at
  BEFORE UPDATE ON edge_agents
  FOR EACH ROW
  EXECUTE FUNCTION update_edge_agents_updated_at();

CREATE OR REPLACE FUNCTION update_blueprints_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_blueprints_updated_at
  BEFORE UPDATE ON blueprints
  FOR EACH ROW
  EXECUTE FUNCTION update_blueprints_updated_at();
