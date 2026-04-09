"""
L3 进程内合并项目根与统帅目录 ``.env`` 到 ``os.environ``。

- ``l3_node.__main__`` 入口与 ``resolve_mcp_cfg_placeholders`` / ``MCPManager.start`` **共用**，
  避免「仅 import 子模块、未执行 __main__」或「MCP 早于 dotenv 执行」时 ``TAVILY_API_KEY`` 未加载。
- 可 **重复调用**：后续调用仍会尝试合并统帅目录（``override`` 由环境变量控制），成本低。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Callable, Optional

TraceCb = Optional[Callable[..., Any]]

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _jachin_home_dotenv_path() -> Path:
    jh = (os.environ.get("JACHIN_HOME") or "").strip()
    if jh:
        return Path(jh).expanduser().resolve() / ".env"
    return Path.home() / ".jachin" / ".env"


def merge_l3_dotenv_into_os(
    *,
    l3_project_root: Optional[str] = None,
    trace_cb: TraceCb = None,
) -> bool:
    """
    合并项目 ``.env`` 与 ``$JACHIN_HOME/.env`` / ``~/.jachin/.env``。

    :param l3_project_root: ``l3_node.__main__`` 推导的仓库根（含 ``l3_node`` 的父目录），可选。
    :param trace_cb: 与 ``early_log.trace`` 兼容的 ``(fmt, *args)`` 回调，可选。
    :return: 是否成功执行过至少一次 ``load_dotenv``（无 python-dotenv 则为 False）。

    环境变量 ``JACHIN_DOTENV_MERGE_DISABLE=1``：跳过合并（仅单元测试隔离用）。
    """
    def _t(fmt: str, *args: Any) -> None:
        if trace_cb:
            try:
                trace_cb(fmt, *args)
            except Exception:
                pass

    if (os.environ.get("JACHIN_DOTENV_MERGE_DISABLE") or "").strip().lower() in ("1", "true", "yes"):
        _t("dotenv merge disabled by JACHIN_DOTENV_MERGE_DISABLE")
        return False

    try:
        from dotenv import load_dotenv
    except ImportError:
        _t("dotenv ImportError, skip merge")
        return False

    _env_loaded = False
    try:
        if getattr(sys, "frozen", False):
            for _p in (Path.cwd() / ".env", Path.home() / ".jachin" / ".env"):
                _t(".env path=%s exists=%s", _p, _p.exists())
                if _p.exists():
                    load_dotenv(_p, encoding="utf-8")
                    _env_loaded = True
                    _t(".env loaded from %s (frozen)", _p)
                    break
        else:
            roots: list[Path] = []
            ja = (os.environ.get("JACHIN_APP_ROOT") or "").strip()
            if ja:
                roots.append(Path(ja).expanduser().resolve())
            if l3_project_root:
                roots.append(Path(l3_project_root).expanduser().resolve())
            # cwd 优先于本模块推导的仓库根：便于在子目录/测试临时目录运行时先读当前目录 .env
            roots.append(Path.cwd().resolve())
            roots.append(_REPO_ROOT)
            seen: set[str] = set()
            for base in roots:
                if not base or str(base) in seen:
                    continue
                seen.add(str(base))
                p = base / ".env"
                _t(".env path=%s exists=%s", p, p.exists())
                if p.exists():
                    load_dotenv(p, encoding="utf-8")
                    _env_loaded = True
                    _t(".env loaded from %s", p)
                    break
            if not _env_loaded:
                _cwd = Path.cwd().resolve()
                for _ in range(8):
                    _p = _cwd / ".env"
                    _t(".env cwd-walk path=%s exists=%s", _p, _p.exists())
                    if _p.exists():
                        load_dotenv(_p, encoding="utf-8")
                        _env_loaded = True
                        _t(".env loaded from %s (cwd search)", _p)
                        break
                    _par = _cwd.parent
                    if _par == _cwd:
                        break
                    _cwd = _par
                    if not _cwd or str(_cwd) == "/":
                        break

        _jh = _jachin_home_dotenv_path()
        if _jh.exists():
            _ov = (os.environ.get("JACHIN_HOME_DOTENV_OVERRIDE_PROJECT") or "").strip().lower() in (
                "1",
                "true",
                "yes",
            )
            load_dotenv(_jh, encoding="utf-8", override=_ov)
            _env_loaded = True
            _t(".env merged from %s (jachin home, override=%s)", _jh, _ov)
        elif not _env_loaded:
            _t("dotenv: no .env loaded; jachin home missing at %s", _jh)

        _t("dotenv merge done, any_loaded=%s", _env_loaded)
    except Exception as e:
        _t("dotenv merge Exception: %s", e)
        return False
    return True
