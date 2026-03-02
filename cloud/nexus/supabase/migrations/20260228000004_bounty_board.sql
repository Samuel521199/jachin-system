-- =============================================================================
-- 战役 B：造血中枢 - 悬赏大厅 (Bounty Board) 底层数据库架构
-- 企业资金托管与极客交付的防篡改骨架
-- 企业用户与极客开发者均存在于 auth.users 表中
-- =============================================================================

-- 1. 悬赏任务表 (bounties)
-- 企业发布需求，设置赏金与测试用例
CREATE TABLE IF NOT EXISTS bounties (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  enterprise_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  description TEXT,
  reward_amount NUMERIC(12, 2) NOT NULL CHECK (reward_amount > 0),
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'in_progress', 'completed', 'cancelled')),
  test_cases_cid VARCHAR(128),  -- IPFS 哈希，测试用例存储位置
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE bounties IS '悬赏大厅 - 企业发布的开发任务';
COMMENT ON COLUMN bounties.enterprise_id IS '发布悬赏的企业用户 (auth.users)';
COMMENT ON COLUMN bounties.test_cases_cid IS 'IPFS CID，自动化测试用例，用于验证极客交付';
COMMENT ON COLUMN bounties.status IS 'open=开放接单, in_progress=极客已接单, completed=交付通过, cancelled=已取消';

CREATE INDEX idx_bounties_enterprise_id ON bounties(enterprise_id);
CREATE INDEX idx_bounties_status ON bounties(status);
CREATE INDEX idx_bounties_created_at ON bounties(created_at DESC);

-- 2. 悬赏接单表 (bounty_applications)
-- 极客接单、交付 JMP、自动化测试更新状态
CREATE TABLE IF NOT EXISTS bounty_applications (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  bounty_id UUID NOT NULL REFERENCES bounties(id) ON DELETE CASCADE,
  geek_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'in_progress' CHECK (status IN ('in_progress', 'submitted', 'passed', 'failed', 'withdrawn')),
  submitted_jmp_cid VARCHAR(128),  -- 交付的 JMP 模块 IPFS 哈希
  submitted_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(bounty_id)  -- 每个悬赏仅允许一名极客接单
);

COMMENT ON TABLE bounty_applications IS '极客接单与交付记录';
COMMENT ON COLUMN bounty_applications.geek_id IS '接单极客 (auth.users)';
COMMENT ON COLUMN bounty_applications.submitted_jmp_cid IS '交付的 JMP 包 IPFS CID';
COMMENT ON COLUMN bounty_applications.status IS 'in_progress=开发中, submitted=已提交, passed=测试通过(触发 payout), failed=测试失败, withdrawn=极客撤单';

CREATE INDEX idx_bounty_applications_bounty_id ON bounty_applications(bounty_id);
CREATE INDEX idx_bounty_applications_geek_id ON bounty_applications(geek_id);
CREATE INDEX idx_bounty_applications_status ON bounty_applications(status);

-- 3. 资金托管流水表 (escrow_transactions)
-- 记录资金的绝对安全流转：deposit(企业充值) / payout(极客收款) / refund(退款)
CREATE TABLE IF NOT EXISTS escrow_transactions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  bounty_id UUID NOT NULL REFERENCES bounties(id) ON DELETE CASCADE,
  action_type TEXT NOT NULL CHECK (action_type IN ('deposit', 'payout', 'refund')),
  amount NUMERIC(12, 2) NOT NULL,
  recipient_user_id UUID REFERENCES auth.users(id),  -- payout 时指向极客
  resolved_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE escrow_transactions IS '资金托管流水 - 防篡改审计';
COMMENT ON COLUMN escrow_transactions.action_type IS 'deposit=企业充值托管, payout=极客收款, refund=原路退回企业';
COMMENT ON COLUMN escrow_transactions.recipient_user_id IS 'payout 时接收赏金的极客 user_id';

CREATE INDEX idx_escrow_transactions_bounty_id ON escrow_transactions(bounty_id);
CREATE INDEX idx_escrow_transactions_action_type ON escrow_transactions(action_type);
CREATE INDEX idx_escrow_transactions_resolved_at ON escrow_transactions(resolved_at DESC);

-- =============================================================================
-- 触发器逻辑说明（伪代码 / 注释）
-- =============================================================================
-- 当 bounty_applications 的 status 被边缘智能体的自动化测试更新为 'passed' 时，
-- 应触发以下逻辑：
--
-- 1. 在 escrow_transactions 中写入一笔 action_type='payout' 记录
--    - bounty_id = 该 application 的 bounty_id
--    - amount = bounties.reward_amount
--    - recipient_user_id = bounty_applications.geek_id
--
-- 2. 将 bounties.status 更新为 'completed'
--
-- 3. 可选：调用支付网关（Stripe Connect 等）将托管资金实际划拨给极客
--
-- 实现方式建议：
-- - 使用 PostgreSQL 触发器：AFTER UPDATE ON bounty_applications
-- - 当 NEW.status = 'passed' 且 OLD.status != 'passed' 时，INSERT INTO escrow_transactions
-- - 或由 Layer 1 API (PATCH /api/v1/bounties/:id/application) 在业务逻辑中完成
-- =============================================================================
