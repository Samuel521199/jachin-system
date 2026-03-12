#!/usr/bin/env python
"""
架构审计脚本 - 验证 000-structure.mdc 规则
Audit: [MISSING] 和 [VIOLATION] 检查
"""

import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

VIOLATIONS = []
MISSING = []


def check_prohibited_dirs():
    """禁止的 core/ 子目录"""
    prohibited = ["core/schedule", "core/scheduler"]
    for p in prohibited:
        path = project_root / p
        if path.exists() and path.is_dir():
            VIOLATIONS.append(f"[VIOLATION] Prohibited directory exists: {p}")


def check_skills_inherit_base():
    """所有技能必须继承 BaseSkillActor"""
    skills_repo = project_root / "skills_repo" / "_bundled"
    if not skills_repo.exists():
        return
    for skill_dir in skills_repo.iterdir():
        if not skill_dir.is_dir():
            continue
        main_py = skill_dir / "main.py"
        if not main_py.exists():
            continue
        content = main_py.read_text(encoding="utf-8")
        if "BaseSkillActor" not in content:
            MISSING.append(f"[MISSING] {skill_dir.name}: does not inherit BaseSkillActor")


def check_plugin_manager_usage():
    """PluginManager 为唯一入口，禁止 SkillRegistry"""
    core_py = list((project_root / "core").rglob("*.py"))
    for f in core_py:
        try:
            text = f.read_text(encoding="utf-8")
            if "SkillRegistry" in text and "deprecated" not in text.lower() and "PluginManager" not in text:
                rel = f.relative_to(project_root)
                if "skill_registry.py" not in str(rel):
                    VIOLATIONS.append(f"[VIOLATION] {rel}: uses SkillRegistry, should use PluginManager")
        except Exception:
            pass


def main():
    check_prohibited_dirs()
    check_skills_inherit_base()
    check_plugin_manager_usage()

    print("=" * 60)
    print("Architecture Audit Report (000-structure.mdc)")
    print("=" * 60)

    if VIOLATIONS:
        print("\n[VIOLATIONS]")
        for v in VIOLATIONS:
            print(f"  {v}")

    if MISSING:
        print("\n[MISSING]")
        for m in MISSING:
            print(f"  {m}")

    if not VIOLATIONS and not MISSING:
        print("\n[OK] All checks passed. No [MISSING] or [VIOLATION] found.")
        return 0

    print(f"\nTotal: {len(VIOLATIONS)} violations, {len(MISSING)} missing")
    return 1


if __name__ == "__main__":
    sys.exit(main())
