-- =============================================================================
-- Jachin Nexus (Layer 1) - 设备配对会话表
-- Device Authorization Grant (RFC 8628) — 6 位码傻瓜式绑定
-- 详见 docs/PAIRING_PROTOCOL_SPEC.md
-- =============================================================================

CREATE TABLE IF NOT EXISTS pairing_sessions (
    session_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    short_code VARCHAR(6) NOT NULL UNIQUE,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',  -- pending | approved | expired
    device_info JSONB,
    user_id UUID REFERENCES nexus_users(id),
    layer2_instance_id UUID REFERENCES layer2_instances(id),
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_pairing_sessions_short_code ON pairing_sessions(short_code);
CREATE INDEX idx_pairing_sessions_expires_at ON pairing_sessions(expires_at);
CREATE INDEX idx_pairing_sessions_status ON pairing_sessions(status);

