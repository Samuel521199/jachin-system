"""从 skills_repo/agent_roles/role_pool.yaml 加载 AGI 路线图中的扩展角色说明，注入 delegate 提示。"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path


def _role_pool_yaml_path() -> Path:
    from l3_node.paths import get_app_root

    return (get_app_root() / "skills_repo" / "agent_roles" / "role_pool.yaml").resolve()


@lru_cache(maxsize=1)
def _load_role_pool_lines() -> tuple[str, ...]:
    p = _role_pool_yaml_path()
    if not p.is_file():
        return ()
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        return ()
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except Exception:
        return ()
    if not isinstance(raw, dict):
        return ()
    roles = raw.get("roles")
    if not isinstance(roles, list):
        return ()
    lines: list[str] = []
    for r in roles:
        if not isinstance(r, dict):
            continue
        rid = str(r.get("id") or "").strip()
        name = str(r.get("name") or "").strip()
        desc = str(r.get("description") or "").strip()
        if not rid:
            continue
        if name and desc:
            lines.append(f"- **{rid}**（{name}）→ {desc}")
        elif name:
            lines.append(f"- **{rid}**（{name}）")
        else:
            lines.append(f"- **{rid}** → {desc}" if desc else f"- **{rid}**")
    return tuple(lines)


def format_role_pool_delegate_addon() -> str:
    """追加在 built-in delegate_hint 之后；文件缺失时返回空串。"""
    rows = _load_role_pool_lines()
    if not rows:
        return ""
    body = "\n".join(rows[:16])
    if len(rows) > 16:
        body += f"\n- …（共 {len(rows)} 条，完整见 skills_repo/agent_roles/role_pool.yaml）"
    return f"""

**扩展角色池（AGI 路线图，与内置 role 等价可选，sub_tasks.role 填 id）**：
{body}
"""
