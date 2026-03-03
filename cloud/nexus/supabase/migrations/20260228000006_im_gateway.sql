-- =============================================================================
-- Jachin Nexus (Layer 1) - 进化战役 2：IM 网关
-- 绑定 Telegram / 飞书，消息队列，NAT 穿透的指令下发
-- =============================================================================

-- 1. edge_agents 增加 IM 绑定 ID（如 Telegram Chat ID、飞书 Chat ID）
ALTER TABLE edge_agents
  ADD COLUMN IF NOT EXISTS im_binding_id TEXT,
  ADD COLUMN IF NOT EXISTS im_platform TEXT DEFAULT 'telegram';

CREATE INDEX IF NOT EXISTS idx_edge_agents_im_binding ON edge_agents(im_binding_id) WHERE im_binding_id IS NOT NULL;

COMMENT ON COLUMN edge_agents.im_binding_id IS 'IM 绑定 ID，如 Telegram chat_id、飞书 chat_id，用于 Webhook 路由';
COMMENT ON COLUMN edge_agents.im_platform IS 'IM 平台：telegram | lark';

-- 2. 消息队列表：inbound=用户发来，outbound=回传用户
CREATE TABLE IF NOT EXISTS agent_message_queue (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_id UUID NOT NULL REFERENCES edge_agents(id) ON DELETE CASCADE,
  message_text TEXT NOT NULL,
  direction TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'processed', 'failed')),
  source_meta JSONB DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  processed_at TIMESTAMPTZ
);

CREATE INDEX idx_agent_message_queue_agent_status ON agent_message_queue(agent_id, status);
CREATE INDEX idx_agent_message_queue_created ON agent_message_queue(created_at ASC);

COMMENT ON TABLE agent_message_queue IS 'IM 消息队列：用户发来的指令 (inbound) 下发给 Agent，Agent 执行结果 (outbound) 回传用户';
