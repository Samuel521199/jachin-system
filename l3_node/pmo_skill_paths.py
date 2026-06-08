"""
PMO-Copilot SKILL.md 路径解析（便携包 / 仓库 / L2 同步缓存）。

供 ``run_pmo_copilot_skill.py``、``pmo_lark_trigger`` 等共用，避免仅打脚本不打 ``skills_repo`` 时启动失败。
"""
from __future__ import annotations

import os
from pathlib import Path


def resolve_pmo_skill_md(*, explicit: str | Path | None = None) -> Path | None:
    """
    解析 PMO ``SKILL.md`` 绝对路径。

    ``explicit`` 非空且文件存在时优先；否则按候选顺序探测。
    """
    if explicit:
        p = Path(explicit).expanduser().resolve()
        if p.is_file():
            return p

    try:
        from l3_node.paths import get_app_root

        app_root = get_app_root()
    except Exception:
        app_root = Path(__file__).resolve().parent.parent

    jachin = Path(os.environ.get("JACHIN_HOME") or Path.home() / ".jachin").expanduser()

    candidates: list[Path] = [
        app_root / "skills_repo" / "pmo-copilot" / "SKILL.md",
        jachin / "l3_skill_cache" / "pmo-copilot" / "SKILL.md",
        jachin / "skills" / "pmo-copilot" / "SKILL.md",
        jachin / "l3_mcp_cache" / "com.jachin.pmo-copilot" / "SKILL.md",
        jachin / "inventory" / "skills" / "pmo-copilot" / "SKILL.md",
        Path(__file__).resolve().parent.parent / "skills_repo" / "pmo-copilot" / "SKILL.md",
    ]

    # L2 同步的 Wasm 技能目录名多为 item_id（UUID），不是 pmo-copilot；且当前 skill_sync 只下 wasm，
    # 一般不包含 SKILL.md。仍扫描缓存/inventory，兼容手动放置或将来完整 JSP 包。
    for base in (
        jachin / "l3_skill_cache",
        jachin / "inventory" / "skills",
        jachin / "skills",
    ):
        if not base.is_dir():
            continue
        try:
            for skill_md in base.rglob("SKILL.md"):
                parent = skill_md.parent.name.lower()
                if "pmo" in parent and "copilot" in parent:
                    candidates.append(skill_md)
                elif skill_md.parent.joinpath("plugin.json").is_file():
                    try:
                        import json

                        pj = json.loads(skill_md.parent.joinpath("plugin.json").read_text(encoding="utf-8"))
                        pid = str(pj.get("id") or "").lower()
                        pname = str(pj.get("name") or "").lower()
                        if "pmo" in pid or "pmo" in pname:
                            candidates.append(skill_md)
                    except Exception:
                        pass
        except OSError:
            pass

    try:
        from l3_node.autonomy.skill_evolver import find_skill_md_path

        evo = find_skill_md_path("pmo-copilot")
        if evo is not None:
            candidates.insert(0, evo)
    except Exception:
        pass

    seen: set[str] = set()
    for p in candidates:
        try:
            key = str(p.resolve())
        except OSError:
            key = str(p)
        if key in seen:
            continue
        seen.add(key)
        if p.is_file():
            return p.resolve()
    return None
