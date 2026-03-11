"""
Jachin Nexus V2 - Layer 2 控制面数据库 Schema

子账号、API Key 保险箱、L3 节点注册（含公钥）。
"""
from __future__ import annotations

import sqlite3


def _migrate_sub_accounts_pairing_code(conn: sqlite3.Connection) -> None:
    """迁移：为已有 sub_accounts 表添加 l1_pairing_code 列（若不存在）"""
    cur = conn.execute("PRAGMA table_info(sub_accounts)")
    cols = [row[1] for row in cur.fetchall()]
    if "l1_pairing_code" not in cols:
        conn.execute("ALTER TABLE sub_accounts ADD COLUMN l1_pairing_code TEXT")
        conn.commit()


def _migrate_l3_nodes_model_endpoints(conn: sqlite3.Connection) -> None:
    """迁移：为 l3_nodes 添加 model_endpoints 列（JSON，如 {"api-1":"gpt-4o"}）"""
    cur = conn.execute("PRAGMA table_info(l3_nodes)")
    cols = [row[1] for row in cur.fetchall()]
    if "model_endpoints" not in cols:
        conn.execute("ALTER TABLE l3_nodes ADD COLUMN model_endpoints TEXT DEFAULT '{}'")
        conn.commit()


def _migrate_sub_accounts_resource_quota(conn: sqlite3.Connection) -> None:
    """迁移：为 sub_accounts 添加 resource_quota 列（JSON：max_memory_gb, monthly_task_limit）"""
    cur = conn.execute("PRAGMA table_info(sub_accounts)")
    cols = [row[1] for row in cur.fetchall()]
    if "resource_quota" not in cols:
        conn.execute("ALTER TABLE sub_accounts ADD COLUMN resource_quota TEXT DEFAULT '{}'")
        conn.commit()


def _migrate_l3_nodes_api_key_id(conn: sqlite3.Connection) -> None:
    """迁移：为 l3_nodes 添加 api_key_ids 列（JSON 数组，关联 api_keys_vault.id，该节点专属 Key）"""
    cur = conn.execute("PRAGMA table_info(l3_nodes)")
    cols = [row[1] for row in cur.fetchall()]
    if "api_key_ids" not in cols:
        conn.execute("ALTER TABLE l3_nodes ADD COLUMN api_key_ids TEXT DEFAULT '[]'")
        conn.commit()


def _migrate_l3_nodes_trust_zone(conn: sqlite3.Connection) -> None:
    """迁移：为 l3_nodes 添加 trust_zone 列（网络亲和性：局域网标识）"""
    cur = conn.execute("PRAGMA table_info(l3_nodes)")
    cols = [row[1] for row in cur.fetchall()]
    if "trust_zone" not in cols:
        conn.execute("ALTER TABLE l3_nodes ADD COLUMN trust_zone TEXT DEFAULT ''")
        conn.commit()


def _migrate_l3_nodes_display_name(conn: sqlite3.Connection) -> None:
    """迁移：为 l3_nodes 添加 display_name 列（用户自定义设备名，便于 L2 审批识别）"""
    cur = conn.execute("PRAGMA table_info(l3_nodes)")
    cols = [row[1] for row in cur.fetchall()]
    if "display_name" not in cols:
        conn.execute("ALTER TABLE l3_nodes ADD COLUMN display_name TEXT DEFAULT ''")
        conn.commit()


def _migrate_sub_accounts_iam(conn: sqlite3.Connection) -> None:
    """迁移：为 sub_accounts 添加 department, role_id, is_active（IAM 层级化）"""
    cur = conn.execute("PRAGMA table_info(sub_accounts)")
    cols = [row[1] for row in cur.fetchall()]
    if "department" not in cols:
        conn.execute("ALTER TABLE sub_accounts ADD COLUMN department TEXT DEFAULT ''")
        conn.commit()
    if "role_id" not in cols:
        conn.execute("ALTER TABLE sub_accounts ADD COLUMN role_id TEXT DEFAULT ''")
        conn.commit()
    if "is_active" not in cols:
        conn.execute("ALTER TABLE sub_accounts ADD COLUMN is_active INTEGER DEFAULT 1")
        conn.commit()


def _migrate_coordinate_subtasks_timeout(conn: sqlite3.Connection) -> None:
    """迁移：为 coordinate_subtasks 添加 timeout_seconds 列"""
    cur = conn.execute("PRAGMA table_info(coordinate_subtasks)")
    cols = [row[1] for row in cur.fetchall()]
    if "timeout_seconds" not in cols:
        conn.execute("ALTER TABLE coordinate_subtasks ADD COLUMN timeout_seconds REAL")
        conn.commit()


def _migrate_permissions_to_structured(conn: sqlite3.Connection) -> None:
    """迁移：将 permissions_json 扁平数据迁移至 sub_account_permissions 表"""
    import json
    import secrets
    rows = conn.execute(
        "SELECT id, permissions_json FROM sub_accounts WHERE permissions_json IS NOT NULL AND permissions_json != ''"
    ).fetchall()
    for row in rows:
        sub_account_id = row[0]
        try:
            perms = json.loads(row[1] or "{}")
        except json.JSONDecodeError:
            continue
        existing = conn.execute(
            "SELECT 1 FROM sub_account_permissions WHERE sub_account_id = ? LIMIT 1",
            (sub_account_id,),
        ).fetchone()
        if existing:
            continue
        inserts: list[tuple[str, str, str, str, str]] = []
        for key, default in [
            ("can_coordinate", True),
            ("can_memory_read", True),
            ("can_memory_write", True),
            ("can_keys_read", True),
        ]:
            val = perms.get(key, default)
            action = "allow" if val else "deny"
            rid = secrets.token_hex(4)
            inserts.append((f"sap-{rid}", sub_account_id, "service_switch", key, action))
        node_ids = perms.get("l3_node_ids")
        if isinstance(node_ids, list):
            for nid in node_ids:
                if isinstance(nid, str) and nid.strip():
                    rid = secrets.token_hex(4)
                    inserts.append((f"sap-{rid}", sub_account_id, "l3_node", nid.strip(), "keys:read"))
        skills = perms.get("allowed_skills") or perms.get("skill_whitelist")
        if skills is not None:
            if not isinstance(skills, list):
                skills = []
            if len(skills) == 0:
                rid = secrets.token_hex(4)
                inserts.append((f"sap-{rid}", sub_account_id, "skill", "__none__", "deny"))
            else:
                for s in skills:
                    if isinstance(s, str) and s.strip():
                        sk = s.strip().lower()
                        sk = sk if ":" in sk else f"core:{sk}"
                        rid = secrets.token_hex(4)
                        inserts.append((f"sap-{rid}", sub_account_id, "skill", sk, "execute"))
        else:
            rid = secrets.token_hex(4)
            inserts.append((f"sap-{rid}", sub_account_id, "skill", "*", "allow"))
        switches = perms.get("service_switches")
        if switches is not None:
            if not isinstance(switches, list):
                switches = []
            for sw in switches:
                if isinstance(sw, (str, int)) and str(sw).strip():
                    rid = secrets.token_hex(4)
                    inserts.append((f"sap-{rid}", sub_account_id, "service_switch", f"service:{str(sw).strip().lower()}", "delegate"))
        ns_list = perms.get("allowed_memory_namespaces")
        if isinstance(ns_list, list):
            for ns in ns_list:
                if isinstance(ns, str) and ns.strip():
                    rid = secrets.token_hex(4)
                    inserts.append((f"sap-{rid}", sub_account_id, "memory_namespace", ns.strip(), "read"))
        if not inserts:
            rid = secrets.token_hex(4)
            inserts.append((f"sap-{rid}", sub_account_id, "service_switch", "_migrated", "allow"))
        for t in inserts:
            conn.execute(
                """
                INSERT OR IGNORE INTO sub_account_permissions (id, sub_account_id, resource_type, resource_id, action)
                VALUES (?, ?, ?, ?, ?)
                """,
                t,
            )
    conn.commit()


def _load_nexus_config() -> dict:
    """读取 nexus_config.json，获取 l1_user_id（L1 配对透传）。支持 env 覆盖（Docker 部署）"""
    import json
    import os
    from pathlib import Path
    cfg = {}
    path = Path.home() / ".jachin" / "nexus_config.json"
    if path.exists():
        try:
            cfg = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    if os.environ.get("NEXUS_INSTANCE_ID"):
        cfg["instance_id"] = os.environ["NEXUS_INSTANCE_ID"]
    if os.environ.get("NEXUS_L1_USER_ID"):
        cfg["l1_user_id"] = os.environ["NEXUS_L1_USER_ID"]
    elif os.environ.get("NEXUS_INSTANCE_ID"):
        cfg["l1_user_id"] = os.environ["NEXUS_INSTANCE_ID"]
    return cfg


def _ensure_default_gateway_admin(conn: sqlite3.Connection) -> None:
    """首次启动时创建默认网关管理员 admin/admin123（若表为空）。main_user_id 优先使用 L1 透传的 l1_user_id。"""
    row = conn.execute("SELECT id, main_user_id FROM gateway_admins LIMIT 1").fetchone()
    cfg = _load_nexus_config()
    l1_user_id = cfg.get("l1_user_id") or cfg.get("instance_id") or ""
    if row:
        if l1_user_id and row[1] != l1_user_id:
            conn.execute(
                "UPDATE gateway_admins SET main_user_id = ? WHERE id = ?",
                (l1_user_id, row[0]),
            )
            conn.commit()
        return
    import secrets
    import bcrypt
    admin_id = f"gw-admin-{secrets.token_hex(4)}"
    default_user = "admin"
    default_pass = "admin123"
    pw_hash = bcrypt.hashpw(default_pass.encode("utf-8"), bcrypt.gensalt()).decode("ascii")
    main_user_id = l1_user_id or admin_id
    conn.execute(
        """
        INSERT INTO gateway_admins (id, username, password_hash, main_user_id, role)
        VALUES (?, ?, ?, ?, 'admin')
        """,
        (admin_id, default_user, pw_hash, main_user_id),
    )
    conn.commit()


def init_all(conn: sqlite3.Connection) -> None:
    """初始化所有 L2 控制面表"""
    conn.executescript(_SCHEMA_SQL)
    _migrate_sub_accounts_pairing_code(conn)
    _migrate_sub_accounts_iam(conn)
    _migrate_sub_accounts_resource_quota(conn)
    _migrate_l3_nodes_model_endpoints(conn)
    _migrate_l3_nodes_api_key_id(conn)
    _migrate_l3_nodes_trust_zone(conn)
    _migrate_l3_nodes_display_name(conn)
    _migrate_coordinate_subtasks_timeout(conn)
    _migrate_permissions_to_structured(conn)
    _ensure_default_gateway_admin(conn)
    conn.commit()


_SCHEMA_SQL = """
-- =============================================================================
-- L2 控制面 - 网关管理员（本地登录，JWT 鉴权）
-- 私有化部署时与 instance_id 绑定，main_user_id 用于子账号归属
-- =============================================================================
CREATE TABLE IF NOT EXISTS gateway_admins (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    main_user_id TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'admin',
    created_at REAL DEFAULT (strftime('%s', 'now')),
    updated_at REAL DEFAULT (strftime('%s', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_gateway_admins_username ON gateway_admins(username);
CREATE INDEX IF NOT EXISTS idx_gateway_admins_main_user ON gateway_admins(main_user_id);

-- =============================================================================
-- L2 控制面 - 子账号
-- 由用户主账号在 L2 创建，permissions_json 定义允许的 L3 节点和 Skill 白名单
-- =============================================================================
CREATE TABLE IF NOT EXISTS sub_accounts (
    id TEXT PRIMARY KEY,
    main_user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'member',
    permissions_json TEXT NOT NULL DEFAULT '{}',
    l1_pairing_code TEXT,
    created_at REAL DEFAULT (strftime('%s', 'now')),
    updated_at REAL DEFAULT (strftime('%s', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_sub_accounts_main_user ON sub_accounts(main_user_id);

-- =============================================================================
-- L2 控制面 - 结构化权限（K8s Ready RBAC）
-- resource_type: service_switch | l3_node | skill | memory_namespace
-- resource_id: can_coordinate | node_id | core:fs_read | customer_service_kb 等
-- action: allow | deny | keys:read | execute | delegate
-- =============================================================================
CREATE TABLE IF NOT EXISTS sub_account_permissions (
    id TEXT PRIMARY KEY,
    sub_account_id TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    action TEXT NOT NULL,
    created_at REAL DEFAULT (strftime('%s', 'now')),
    FOREIGN KEY (sub_account_id) REFERENCES sub_accounts(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_sap_unique
    ON sub_account_permissions(sub_account_id, resource_type, resource_id, action);
CREATE INDEX IF NOT EXISTS idx_sap_sub_account ON sub_account_permissions(sub_account_id);
CREATE INDEX IF NOT EXISTS idx_sap_resource ON sub_account_permissions(resource_type, resource_id);

-- =============================================================================
-- L2 控制面 - L3 节点注册
-- L3 注册时上报设备指纹和公钥，用于 API Key 加密下发
-- =============================================================================
CREATE TABLE IF NOT EXISTS l3_nodes (
    id TEXT PRIMARY KEY,
    device_fingerprint TEXT,
    public_key_pem TEXT NOT NULL,
    sub_account_id TEXT,
    capabilities_json TEXT DEFAULT '{}',
    model_endpoints TEXT DEFAULT '{}',
    last_seen_at REAL DEFAULT (strftime('%s', 'now')),
    created_at REAL DEFAULT (strftime('%s', 'now')),
    FOREIGN KEY (sub_account_id) REFERENCES sub_accounts(id)
);

CREATE INDEX IF NOT EXISTS idx_l3_nodes_sub_account ON l3_nodes(sub_account_id);
CREATE INDEX IF NOT EXISTS idx_l3_nodes_fingerprint ON l3_nodes(device_fingerprint);

-- =============================================================================
-- L2 控制面 - API Key 保险箱
-- L2 用 Master Key 对称加密存储，下发给 L3 时用 L3 公钥加密
-- =============================================================================
CREATE TABLE IF NOT EXISTS api_keys_vault (
    id TEXT PRIMARY KEY,
    sub_account_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    encrypted_key TEXT NOT NULL,
    key_hash TEXT NOT NULL,
    created_at REAL DEFAULT (strftime('%s', 'now')),
    FOREIGN KEY (sub_account_id) REFERENCES sub_accounts(id)
);

CREATE INDEX IF NOT EXISTS idx_api_keys_sub_account ON api_keys_vault(sub_account_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_provider ON api_keys_vault(provider);

-- =============================================================================
-- L2 控制面 - 记忆碎片（L3 同步，梦境优化后持久化）
-- =============================================================================
CREATE TABLE IF NOT EXISTS memory_fragments (
    id TEXT PRIMARY KEY,
    sub_account_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at REAL DEFAULT (strftime('%s', 'now')),
    FOREIGN KEY (sub_account_id) REFERENCES sub_accounts(id)
);

CREATE INDEX IF NOT EXISTS idx_memory_sub_account ON memory_fragments(sub_account_id);
CREATE INDEX IF NOT EXISTS idx_memory_node ON memory_fragments(node_id);

-- =============================================================================
-- L2 控制面 - 协同任务（L3 请求多节点协同，L2 调度）
-- =============================================================================
CREATE TABLE IF NOT EXISTS coordinate_tasks (
    id TEXT PRIMARY KEY,
    sub_account_id TEXT NOT NULL,
    parent_node_id TEXT NOT NULL,
    intent TEXT,
    skill_required TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    result_json TEXT,
    created_at REAL DEFAULT (strftime('%s', 'now')),
    updated_at REAL DEFAULT (strftime('%s', 'now')),
    FOREIGN KEY (sub_account_id) REFERENCES sub_accounts(id)
);

CREATE TABLE IF NOT EXISTS coordinate_subtasks (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    assignee_node_id TEXT NOT NULL,
    intent TEXT NOT NULL,
    skill_required TEXT,
    input_data TEXT,
    timeout_seconds REAL,
    status TEXT NOT NULL DEFAULT 'pending',
    result_json TEXT,
    created_at REAL DEFAULT (strftime('%s', 'now')),
    updated_at REAL DEFAULT (strftime('%s', 'now')),
    FOREIGN KEY (task_id) REFERENCES coordinate_tasks(id)
);

CREATE INDEX IF NOT EXISTS idx_coordinate_tasks_sub_account ON coordinate_tasks(sub_account_id);
CREATE INDEX IF NOT EXISTS idx_coordinate_tasks_status ON coordinate_tasks(status);
CREATE INDEX IF NOT EXISTS idx_coordinate_subtasks_task ON coordinate_subtasks(task_id);
CREATE INDEX IF NOT EXISTS idx_coordinate_subtasks_assignee ON coordinate_subtasks(assignee_node_id);
CREATE INDEX IF NOT EXISTS idx_coordinate_subtasks_status ON coordinate_subtasks(status);

-- =============================================================================
-- L2 IAM - 角色与权限矩阵（与 L1 同步，极速 RBAC 拦截）
-- roles: 企业内角色定义（财务、研发、高管）
-- role_permissions: role_id -> item_id（mcp:xxx, skill:xxx）
-- =============================================================================
CREATE TABLE IF NOT EXISTS roles (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    role_id TEXT NOT NULL,
    name TEXT NOT NULL,
    created_at REAL DEFAULT (strftime('%s', 'now')),
    updated_at REAL DEFAULT (strftime('%s', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_roles_tenant_role ON roles(tenant_id, role_id);

CREATE TABLE IF NOT EXISTS role_permissions (
    id TEXT PRIMARY KEY,
    role_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    created_at REAL DEFAULT (strftime('%s', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_role_permissions_role_item ON role_permissions(role_id, item_id);
CREATE INDEX IF NOT EXISTS idx_role_permissions_item ON role_permissions(item_id);

-- =============================================================================
-- L2 本地审计与用量记录 (usage_telemetry)
-- 供 L1 遥测上报、用量计费、安全审计
-- =============================================================================
CREATE TABLE IF NOT EXISTS usage_telemetry (
    id TEXT PRIMARY KEY,
    sub_account_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    action_name TEXT NOT NULL,
    status TEXT NOT NULL,
    latency_ms REAL,
    timestamp REAL DEFAULT (strftime('%s', 'now')),
    reported INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_usage_telemetry_sub_account ON usage_telemetry(sub_account_id);
CREATE INDEX IF NOT EXISTS idx_usage_telemetry_item ON usage_telemetry(item_id);
CREATE INDEX IF NOT EXISTS idx_usage_telemetry_timestamp ON usage_telemetry(timestamp);
CREATE INDEX IF NOT EXISTS idx_usage_telemetry_reported ON usage_telemetry(reported);

-- =============================================================================
-- Jachin 注册表 (K-V 配置) - 技能级动态提示词、JD_template 等
-- =============================================================================
CREATE TABLE IF NOT EXISTS skill_registry (
    id TEXT PRIMARY KEY,
    skill_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'string',
    created_at REAL DEFAULT (strftime('%s', 'now')),
    updated_at REAL DEFAULT (strftime('%s', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_skill_registry_skill_key ON skill_registry(skill_id, key);
CREATE INDEX IF NOT EXISTS idx_skill_registry_skill ON skill_registry(skill_id);

-- =============================================================================
-- 动态数据卷绑定 - 技能与 VFS 卷的映射，引用计数用于 GC
-- =============================================================================
CREATE TABLE IF NOT EXISTS volume_bindings (
    id TEXT PRIMARY KEY,
    volume_name TEXT NOT NULL,
    skill_id TEXT NOT NULL,
    access_mode TEXT NOT NULL DEFAULT 'rw',
    created_at REAL DEFAULT (strftime('%s', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_volume_bindings_skill ON volume_bindings(skill_id);
CREATE INDEX IF NOT EXISTS idx_volume_bindings_volume ON volume_bindings(volume_name);
"""
