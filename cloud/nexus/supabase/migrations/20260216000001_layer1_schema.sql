-- =============================================================================
-- Jachin Nexus (Layer 1) - 灵界数据字典
-- 战役一：铸造底层数据字典
-- RBAC + plugins_registry + personas_library + transactions
-- =============================================================================

-- 1. 角色枚举与 RBAC
CREATE TYPE nexus_role AS ENUM ('super_admin', 'developer', 'consumer');

-- 2. 用户表（扩展或独立于 Layer 2 用户）
CREATE TABLE IF NOT EXISTS nexus_users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  external_id TEXT UNIQUE NOT NULL,           -- Jachin ID / Passkey / Web3 统一标识
  email TEXT,
  display_name TEXT,
  role nexus_role NOT NULL DEFAULT 'consumer',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_nexus_users_external_id ON nexus_users(external_id);
CREATE INDEX idx_nexus_users_role ON nexus_users(role);

-- 3. 技能插件表 (plugins_registry)
CREATE TABLE IF NOT EXISTS plugins_registry (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  plugin_id TEXT NOT NULL UNIQUE,             -- 反向域名，如 com.jachin.weather
  name TEXT NOT NULL,
  version TEXT NOT NULL,
  description TEXT,
  author_id UUID REFERENCES nexus_users(id),
  author_name TEXT,                           -- 冗余，便于展示
  download_url TEXT NOT NULL,                 -- IPFS CID 或 S3 路径
  download_hash TEXT,                         -- 内容哈希，用于校验
  manifest_json JSONB NOT NULL,               -- manifest.json 完整内容
  runtime_env JSONB DEFAULT '{}',             -- Python 版本、依赖等
  permissions JSONB DEFAULT '[]',             -- 所需权限列表
  category TEXT,                              -- skill | persona | memory
  status TEXT NOT NULL DEFAULT 'pending',     -- pending | approved | rejected
  download_count INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_plugins_registry_plugin_id ON plugins_registry(plugin_id);
CREATE INDEX idx_plugins_registry_status ON plugins_registry(status);
CREATE INDEX idx_plugins_registry_category ON plugins_registry(category);
CREATE INDEX idx_plugins_registry_download_count ON plugins_registry(download_count DESC);

-- 4. 人设灵魂表 (personas_library) - 严格 UUID 全局唯一
CREATE TABLE IF NOT EXISTS personas_library (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  persona_id UUID NOT NULL UNIQUE,            -- 全局唯一，Layer 2 与云端必须一致
  name TEXT NOT NULL,
  version TEXT NOT NULL,
  description TEXT,
  author_id UUID REFERENCES nexus_users(id),
  author_name TEXT,
  download_url TEXT NOT NULL,
  download_hash TEXT,
  manifest_json JSONB NOT NULL,
  vits_model_path TEXT,                       -- 语音包路径
  prompt_template TEXT,                       -- 性格提示词
  avatar_url TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_personas_library_persona_id ON personas_library(persona_id);
CREATE INDEX idx_personas_library_name ON personas_library(name);

-- 5. 交易记录表 (transactions)
CREATE TABLE IF NOT EXISTS transactions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES nexus_users(id),
  resource_type TEXT NOT NULL,                -- plugin | persona
  resource_id UUID NOT NULL,                  -- plugins_registry.id 或 personas_library.id
  resource_plugin_id TEXT,                    -- 如 com.jachin.weather，便于查询
  action TEXT NOT NULL DEFAULT 'acquire',     -- acquire | renew | revoke
  license_key TEXT,                           -- 颁发的 License Key
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_transactions_user_id ON transactions(user_id);
CREATE INDEX idx_transactions_resource ON transactions(resource_type, resource_id);
CREATE INDEX idx_transactions_created_at ON transactions(created_at);

-- 6. 部署指令表 (deploy_commands) - 战役四：端云握手
CREATE TABLE IF NOT EXISTS deploy_commands (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES nexus_users(id),
  layer2_instance_id TEXT,                    -- 家庭服务器实例标识
  resource_type TEXT NOT NULL,                -- plugin | persona
  resource_id UUID NOT NULL,
  download_url TEXT NOT NULL,
  temp_token TEXT NOT NULL UNIQUE,            -- 临时 Token，用于下载鉴权
  token_expires_at TIMESTAMPTZ NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',     -- pending | delivered | completed | failed
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_deploy_commands_status ON deploy_commands(status);
CREATE INDEX idx_deploy_commands_temp_token ON deploy_commands(temp_token);
CREATE INDEX idx_deploy_commands_layer2_instance ON deploy_commands(layer2_instance_id);
