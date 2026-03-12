"""
Jachin Nexus V2 - L2 子账号细粒度权限校验

permissions_json 结构（sub_accounts 表）:
L2 服务开关: can_coordinate, can_memory_read, can_memory_write, can_keys_read, l3_node_ids
L3 零信任下发（auth/poll 时返回）:
  service_switches: ["coder","writer",...]  # 空=全开，非空=delegate 角色白名单
  allowed_skills: ["core:fs_read",...]      # 空=全开，非空=Skill 白名单，兼容 skill_whitelist
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 支持的动作
ACTION_COORDINATE = "coordinate:task"
ACTION_MEMORY_READ = "memory:read"
ACTION_MEMORY_WRITE = "memory:write"
ACTION_KEYS_READ = "keys:read"


def verify_permissions(
    permissions_json: str | dict,
    action: str,
    *,
    node_id: Optional[str] = None,
) -> tuple[bool, str]:
    """
    校验子账号是否有权限执行指定动作。

    Args:
        permissions_json: 子账号的 permissions_json（字符串或已解析的 dict）
        action: 动作标识，如 coordinate:task, memory:read, memory:write, keys:read
        node_id: 可选，用于 keys:read 时校验 l3_node_ids 白名单

    Returns:
        (allowed, message): 是否允许，及拒绝时的原因
    """
    if isinstance(permissions_json, str):
        try:
            perms = json.loads(permissions_json or "{}")
        except json.JSONDecodeError:
            perms = {}
    else:
        perms = permissions_json or {}

    if action == ACTION_COORDINATE:
        allowed = perms.get("can_coordinate", True)
        return (allowed, "无协同权限" if not allowed else "")

    if action == ACTION_MEMORY_READ:
        allowed = perms.get("can_memory_read", True)
        return (allowed, "无记忆读取权限" if not allowed else "")

    if action == ACTION_MEMORY_WRITE:
        allowed = perms.get("can_memory_write", True)
        return (allowed, "无记忆写入权限" if not allowed else "")

    if action == ACTION_KEYS_READ:
        allowed = perms.get("can_keys_read", True)
        if not allowed:
            return (False, "无 API Key 读取权限")
        node_ids = perms.get("l3_node_ids")
        if node_ids and isinstance(node_ids, list) and node_id and node_id not in node_ids:
            return (False, f"节点 {node_id} 不在允许列表中")
        return (True, "")

    return (False, f"未知动作: {action}")


def verify_memory_namespace(
    permissions_json: str | dict,
    namespace_or_list: str | list[str] | None,
    *,
    write: bool = False,
) -> tuple[bool, str]:
    """
    校验子账号对指定命名空间的记忆访问权限。

    Args:
        permissions_json: 子账号权限（字符串或已解析 dict）
        namespace_or_list: 单个命名空间（write=True）或命名空间列表（write=False，None 表示不限定）
        write: True=写入校验，False=读取/检索校验

    Returns:
        (allowed, message)
    """
    if isinstance(permissions_json, str):
        try:
            perms = json.loads(permissions_json or "{}")
        except json.JSONDecodeError:
            perms = {}
    else:
        perms = permissions_json or {}

    allowed_ns = perms.get("allowed_memory_namespaces")
    if not allowed_ns or not isinstance(allowed_ns, list):
        # 未配置：允许所有命名空间（向后兼容）
        return (True, "")

    allowed_set = set(str(n).strip() for n in allowed_ns if n)

    if write:
        ns = (namespace_or_list or "").strip() if isinstance(namespace_or_list, str) else ""
        if not ns:
            ns = "default"
        if ns not in allowed_set:
            return (False, f"命名空间 {ns} 不在允许列表中")
        return (True, "")

    # 读取/检索
    if namespace_or_list is None:
        return (True, "")
    if isinstance(namespace_or_list, str):
        ns_list = [namespace_or_list.strip() or "default"]
    else:
        ns_list = [str(n).strip() or "default" for n in (namespace_or_list or []) if n]
    for n in ns_list:
        if n not in allowed_set:
            return (False, f"命名空间 {n} 不在允许列表中，禁止越权检索")
    return (True, "")


def get_effective_search_namespaces(permissions_json: str | dict, requested: list[str] | None) -> list[str] | None:
    """
    解析检索时有效的命名空间列表。
    - requested 非空：校验后返回（校验由 verify_memory_namespace 完成，此处仅做解析）
    - requested 为空/None：若配置了 allowed_memory_namespaces 则返回该列表，否则返回 None（不按 namespace 过滤）
    """
    if isinstance(permissions_json, str):
        try:
            perms = json.loads(permissions_json or "{}")
        except json.JSONDecodeError:
            perms = {}
    else:
        perms = permissions_json or {}

    if requested:
        return requested
    allowed_ns = perms.get("allowed_memory_namespaces")
    if allowed_ns and isinstance(allowed_ns, list):
        return [str(n).strip() for n in allowed_ns if n]
    return None


def get_permissions(conn, sub_account_id: str) -> dict[str, Any]:
    """
    从数据库获取子账号权限。
    优先从 sub_account_permissions 表读取（结构化 RBAC），
    若该表无数据则回退到 permissions_json（平滑迁移）。
    """
    row = conn.execute(
        "SELECT permissions_json FROM sub_accounts WHERE id = ?",
        (sub_account_id,),
    ).fetchone()
    if not row:
        return {}
    perm_rows = conn.execute(
        """
        SELECT resource_type, resource_id, action
        FROM sub_account_permissions
        WHERE sub_account_id = ?
        """,
        (sub_account_id,),
    ).fetchall()
    if perm_rows:
        return _build_permissions_from_structured(perm_rows)
    try:
        return json.loads(row["permissions_json"] or "{}")
    except json.JSONDecodeError:
        return {}


def _build_permissions_from_structured(rows: list) -> dict[str, Any]:
    """将 sub_account_permissions 行转换为扁平 permissions 字典"""
    perms: dict[str, Any] = {}
    node_ids: list[str] = []
    allowed_skills: list[str] | None = None
    skill_wildcard_seen = False
    service_switches: list[str] | None = None
    allowed_memory_namespaces: list[str] = []
    for r in rows:
        rt, rid, action = r[0], r[1], r[2]
        if rt == "memory_namespace" and action == "read":
            if rid and rid not in allowed_memory_namespaces:
                allowed_memory_namespaces.append(rid)
        elif rt == "service_switch":
            if rid in ("can_coordinate", "can_memory_read", "can_memory_write", "can_keys_read"):
                perms[rid] = action == "allow"
            elif rid.startswith("service:"):
                if service_switches is None:
                    service_switches = []
                role = rid[8:]
                if role and role not in service_switches:
                    service_switches.append(role)
        elif rt == "l3_node" and action == "keys:read":
            node_ids.append(rid)
        elif rt == "skill" and not skill_wildcard_seen:
            if rid == "*" and action == "allow":
                allowed_skills = None
                skill_wildcard_seen = True
            elif rid == "__none__" and action == "deny":
                allowed_skills = []
            else:
                if allowed_skills is None:
                    allowed_skills = []
                if rid and rid not in allowed_skills:
                    allowed_skills.append(rid)
    if node_ids:
        perms["l3_node_ids"] = node_ids
    if allowed_skills is not None:
        perms["allowed_skills"] = allowed_skills
        perms["skill_whitelist"] = allowed_skills
    if service_switches is not None:
        perms["service_switches"] = service_switches
    if allowed_memory_namespaces:
        perms["allowed_memory_namespaces"] = allowed_memory_namespaces
    for k in ("can_coordinate", "can_memory_read", "can_memory_write", "can_keys_read"):
        if k not in perms:
            perms[k] = True
    return perms


def merge_global_banned_skills(allowed_skills: list[str] | None) -> list[str] | None:
    """
    合并 L1 全局封禁：从 allowed_skills 中剔除 global_banned_skills。
    用于下发 L3 时，确保封禁技能绝不泄露。
    """
    try:
        from core.l1_policy import is_skill_banned
    except ImportError:
        return allowed_skills
    if allowed_skills is None:
        return None
    if not allowed_skills:
        return []
    out = [s for s in allowed_skills if s and not is_skill_banned(s)]
    return out


def normalize_permissions_for_l3(perms: dict[str, Any]) -> dict[str, Any]:
    """
    将 L2 permissions_json 规范化为 L3 可用的扁平化格式。
    用于 auth/poll、get_keys 下发，L3 据此做硬拦截。
    自动合并 L1 global_banned_skills，剔除封禁技能。

    Returns:
        {
            "service_switches": [] | None,   # None=全开，[]=无角色，非空=白名单
            "allowed_skills": [] | None,      # None=全开，[]=无技能，非空=白名单（已剔除全局封禁）
        }
    """
    perms = perms or {}
    # allowed_skills: 未配置=None(全开)，显式配置=按值
    if "allowed_skills" in perms or "skill_whitelist" in perms:
        raw_skills = perms.get("allowed_skills") or perms.get("skill_whitelist") or []
        if not isinstance(raw_skills, list):
            raw_skills = []
        allowed_skills = []
        for s in raw_skills:
            if isinstance(s, str) and s.strip():
                s = s.strip().lower()
                allowed_skills.append(s if ":" in s else f"core:{s}")
        allowed_skills = list(dict.fromkeys(allowed_skills))
    else:
        allowed_skills = None

    # service_switches: 未配置=None(全开)，显式配置=按值
    if "service_switches" in perms:
        raw_switches = perms.get("service_switches") or []
        if not isinstance(raw_switches, list):
            raw_switches = []
        service_switches = [str(s).strip().lower() for s in raw_switches if isinstance(s, (str, int)) and str(s).strip()]
    else:
        service_switches = None

    allowed_skills = merge_global_banned_skills(allowed_skills) if allowed_skills else allowed_skills
    return {
        "service_switches": service_switches,
        "allowed_skills": allowed_skills,
    }
