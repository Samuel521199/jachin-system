"""
K11 Playwright 冒烟子进程入口：由同一 ``l3_node`` 可执行文件 / ``python -m l3_node`` 拉起。

PyInstaller onefile 下 ``sys.executable`` 指向 ``l3_node.exe``，不能用于 ``exe script.py`` 解释 .py；
此处用隐藏子命令加载 ``scripts/test_k11_*.py`` 并调用其 ``main()``。
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

from l3_node.paths import (
    get_app_root,
    k11_games_state_machine_smoke_script_path,
    k11_p2_compat_weaknet_script_path,
    k11_unified_smoke_script_path,
)


def _bootstrap_k11_subprocess_env() -> None:
    """
    K11 子进程在 ``__main__`` 里会早于 ``merge_l3_dotenv_into_os`` 就 ``SystemExit``，
    若不在此补一次合并，打包机上会读不到安装目录 / exe 旁 ``.env`` 与 ``~/.jachin/.env``，
    导致飞书凭证缺失、不同步表格与群卡片。

    须**覆盖**可能来自父进程的错误 ``JACHIN_APP_ROOT``（如 cwd 污染），与 ``get_app_root()`` 一致。
    """
    try:
        root = get_app_root().resolve()
        r = str(root)
        if not r:
            return
        os.environ["JACHIN_APP_ROOT"] = r
    except Exception:
        return
    try:
        from core.l3_dotenv_merge import merge_l3_dotenv_into_os

        merge_l3_dotenv_into_os(l3_project_root=r, trace_cb=None)
    except Exception:
        pass
    try:
        from l3_node.packaged_lark_env import apply_packaged_lark_to_os_environ

        apply_packaged_lark_to_os_environ()
    except Exception:
        pass


def _load_k11_script_module(script: Path, unique_module_name: str) -> ModuleType:
    """
    动态加载 Playwright 冒烟脚本模块。

    **必须在 exec_module 前** 将模块挂入 ``sys.modules``，否则脚本里顶层的 ``@dataclass`` 等
    在解析时会执行 ``sys.modules[cls.__module__]``，若未注册会得到 ``None`` 并触发::

        AttributeError: 'NoneType' object has no attribute '__dict__'
    """
    spec = importlib.util.spec_from_file_location(unique_module_name, script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"spec failed for {script}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[unique_module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def run_k11_unified_sync() -> int:
    _bootstrap_k11_subprocess_env()
    script = k11_unified_smoke_script_path()
    if not script.is_file():
        print(f"[FATAL] 缺少 K11 统合冒烟脚本: {script}", file=sys.stderr)
        return 2
    rest = list(sys.argv[2:])
    sys.argv = [script.name] + rest
    try:
        mod = _load_k11_script_module(script, "_k11_unified_sse_subprocess")
    except Exception as e:
        print(f"[FATAL] 无法加载 K11 统合冒烟模块: {e}", file=sys.stderr)
        return 2
    main_fn = getattr(mod, "main", None)
    if main_fn is None:
        print("[FATAL] 脚本缺少 main()", file=sys.stderr)
        return 2
    return int(main_fn())


def run_k11_games_state_machine_sync() -> int:
    _bootstrap_k11_subprocess_env()
    script = k11_games_state_machine_smoke_script_path()
    if not script.is_file():
        print(f"[FATAL] 缺少 K11 游戏状态机冒烟脚本: {script}", file=sys.stderr)
        return 2
    rest = list(sys.argv[2:])
    sys.argv = [script.name] + rest
    try:
        mod = _load_k11_script_module(script, "_k11_games_state_machine_sse_subprocess")
    except Exception as e:
        print(f"[FATAL] 无法加载 K11 游戏状态机脚本: {e}", file=sys.stderr)
        return 2
    main_fn = getattr(mod, "main", None)
    if main_fn is None:
        print("[FATAL] 脚本缺少 main()", file=sys.stderr)
        return 2
    return int(main_fn())


def run_k11_p2_compat_sync() -> int:
    _bootstrap_k11_subprocess_env()
    script = k11_p2_compat_weaknet_script_path()
    if not script.is_file():
        print(f"[FATAL] 缺少 K11 P2 兼容脚本: {script}", file=sys.stderr)
        return 2
    rest = list(sys.argv[2:])
    sys.argv = [script.name] + rest
    try:
        mod = _load_k11_script_module(script, "_k11_p2_compat_sse_subprocess")
    except Exception as e:
        print(f"[FATAL] 无法加载 K11 P2 脚本: {e}", file=sys.stderr)
        return 2
    main_fn = getattr(mod, "main", None)
    if main_fn is None:
        print("[FATAL] 脚本缺少 main()", file=sys.stderr)
        return 2
    return int(main_fn())
