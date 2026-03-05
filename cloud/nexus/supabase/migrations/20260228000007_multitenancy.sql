-- =============================================================================
-- Jachin Nexus (Layer 1) - 多租户架构 (Multi-tenancy)
-- Platform First: B2B2C 多租户，企业级实体通过 organization_id 隔离
-- =============================================================================

-- 1. 组织表 (organizations)
CREATE TABLE IF NOT EXISTS organizations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  billing_plan TEXT DEFAULT 'free',           -- free | pro | enterprise
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_organizations_billing_plan ON organizations(billing_plan);

-- 2. 组织-用户关联表 (organization_users)
CREATE TYPE org_role AS ENUM ('owner', 'admin', 'member');

CREATE TABLE IF NOT EXISTS organization_users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  role org_role NOT NULL DEFAULT 'member',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(org_id, user_id)
);

CREATE INDEX idx_organization_users_org_id ON organization_users(org_id);
CREATE INDEX idx_organization_users_user_id ON organization_users(user_id);

-- 3. edge_agents 增加 organization_id（为空=个人，非空=企业舰队）
ALTER TABLE edge_agents
  ADD COLUMN IF NOT EXISTS organization_id UUID REFERENCES organizations(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_edge_agents_organization_id ON edge_agents(organization_id);

COMMENT ON COLUMN edge_agents.organization_id IS 'NULL=个人用户设备，非空=企业舰队设备，舰队管理必须按此字段隔离';

-- 4. transactions 增加 organization_id（企业采购的技能订阅可共享给组织内所有设备）
ALTER TABLE transactions
  ADD COLUMN IF NOT EXISTS organization_id UUID REFERENCES organizations(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_transactions_organization_id ON transactions(organization_id);

COMMENT ON COLUMN transactions.organization_id IS 'NULL=个人购买，非空=企业采购，组织内 edge_agents 可共享';

-- 5. blueprints 增加 organization_id（企业共享蓝图）
ALTER TABLE blueprints
  ADD COLUMN IF NOT EXISTS organization_id UUID REFERENCES organizations(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_blueprints_organization_id ON blueprints(organization_id);

COMMENT ON COLUMN blueprints.organization_id IS 'NULL=个人蓝图，非空=企业共享蓝图';
