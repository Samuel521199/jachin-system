"""
PMO Copilot CLI 入口（L3 侧车 ``--run-pmo-copilot`` 与开发机脚本共用）。

打包后桌面端通过 ``bin/l3_node-<triple>.exe --run-pmo-copilot`` 启动，
不依赖目标机安装 Python 或完整仓库树。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def resolve_pmo_copilot_script() -> Path | None:
    """定位 ``run_pmo_copilot_skill.py``（便携目录 / 仓库 / PyInstaller _MEIPASS）。"""
    candidates: list[Path] = []

    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        candidates.append(Path(sys._MEIPASS) / "scripts" / "run_pmo_copilot_skill.py")

    try:
        from l3_node.paths import get_app_root

        root = get_app_root()
        candidates.append(root / "scripts" / "run_pmo_copilot_skill.py")
        candidates.append(root / "skills_repo" / "pmo-copilot" / "run_pmo_copilot_skill.py")
    except Exception:
        pass

    repo_root = Path(__file__).resolve().parent.parent
    candidates.append(repo_root / "scripts" / "run_pmo_copilot_skill.py")

    seen: set[str] = set()
    for p in candidates:
        key = str(p.resolve()) if p.exists() else str(p)
        if key in seen:
            continue
        seen.add(key)
        if p.is_file():
            return p
    return None


def _argv_without_sidecar_flag(argv: list[str] | None) -> list[str]:
    raw = list(argv if argv is not None else sys.argv)
    if not raw:
        return raw
    return [raw[0]] + [a for a in raw[1:] if a != "--run-pmo-copilot"]


def run_pmo_copilot_main(argv: list[str] | None = None) -> int:
    """执行 PMO Copilot 全流程；``argv`` 默认取 ``sys.argv``（可含 ``--run-pmo-copilot``）。"""
    raw = _argv_without_sidecar_flag(argv)

    # PyInstaller 侧车：l3_node 已在当前进程，优先用内嵌脚本，避免 load 安装目录副本污染 sys.path
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        bundled = Path(sys._MEIPASS) / "scripts" / "run_pmo_copilot_skill.py"
        if bundled.is_file():
            prev_argv = sys.argv
            try:
                sys.argv = [str(bundled), *raw[1:]] if raw else [str(bundled)]
                spec = importlib.util.spec_from_file_location("jachin_pmo_copilot_bundled", bundled)
                if spec is None or spec.loader is None:
                    print(f"无法加载内嵌 PMO 入口: {bundled}", file=sys.stderr)
                    return 1
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                main_fn = getattr(mod, "main", None)
                if callable(main_fn):
                    return int(main_fn())
            finally:
                sys.argv = prev_argv
            return 0

    script = resolve_pmo_copilot_script()
    if script is None:
        print(
            "找不到 PMO Copilot 入口（scripts/run_pmo_copilot_skill.py）。"
            "请确认安装包含 scripts/ 与 skills_repo/pmo-copilot，或从仓库根运行。",
            file=sys.stderr,
        )
        return 1

    if not raw:
        raw = [str(script)]

    spec = importlib.util.spec_from_file_location("jachin_pmo_copilot_entry", script)
    if spec is None or spec.loader is None:
        print(f"无法加载 PMO 入口: {script}", file=sys.stderr)
        return 1

    mod = importlib.util.module_from_spec(spec)
    prev_argv = sys.argv
    try:
        sys.argv = raw
        spec.loader.exec_module(mod)
    finally:
        sys.argv = prev_argv

    main_fn = getattr(mod, "main", None)
    if callable(main_fn):
        return int(main_fn())
    return 0
