"""
启动时修正 ~/.jachin/mcp_servers.json 中过期的 hr-atomic-tools 路径，避免整条 MCP 握手被无效配置干扰；
并在用户尚未配置任何官方 server-filesystem 时，自动追加一条默认条目（与 skills_repo 插件同 id，避免双实例）；
尚未配置 **mcp-server-fetch**（``python -m mcp_server_fetch``）时追加官方 URL 抓取 MCP（工具名多为 ``fetch``）。

**robots.txt**：官方 fetch 默认遵守站点规则（如头条 ``Disallow: /trending/`` 会导致工具返回错误而非崩溃）。
若你确认自用场景可承担合规风险，可设环境变量 ``JACHIN_MCP_FETCH_IGNORE_ROBOTS=1``（或 ``true``/``yes``/``on``）：
启动时会为已配置的 ``-m mcp_server_fetch`` 条目自动追加 CLI 参数 ``--ignore-robots-txt``（亦作用于自动补全的默认 Fetch 项）。

由 l3_node.primitives.mcp.mcp_stdio_bootstrap 在 MCPManager.start() 之前调用。

**说明**：Anthropic 官方 Fetch 在 **PyPI**（``mcp-server-fetch``），**无** npm ``@modelcontextprotocol/server-fetch`` 包；勿在配置里写错误的 npx 包名。

**SQLite 生活库**：若尚未配置 ``uvx mcp-server-sqlite``（``sqlite_manager``），可由 ``ensure_sqlite_manager_life_db_mcp`` 自动合并一条（需本机 ``uv``/``uvx``）。

**YouTube 字幕**：已改为 L3 **原生工具** ``core:youtube_transcript``（``youtube-transcript-api``，见 ``l3_node/skills/native_tools/youtube_transcript_tools.py``），
不再由本文件自动注入 npm ``youtube-transcript`` MCP。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_HR_SERVER_IDS_LOWER = frozenset({"hr-atomic-tools"})

_DEFAULT_FILESYSTEM_MCP_ID = "com.jachin.mcp.filesystem_workspace"
_DEFAULT_FETCH_MCP_ID = "official-mcp-fetch"


def _mcp_entry_has_official_filesystem(entry: dict[str, Any]) -> bool:
    sid = str(entry.get("id") or entry.get("name") or "").strip()
    if sid == _DEFAULT_FILESYSTEM_MCP_ID:
        return True
    args = entry.get("args")
    if isinstance(args, list):
        return any(isinstance(a, str) and "server-filesystem" in a for a in args)
    return False


def ensure_default_official_filesystem_mcp() -> bool:
    """
    若 ``~/.jachin/mcp_servers.json`` 中尚无任何 ``@modelcontextprotocol/server-filesystem`` 配置，
    则创建或合并写入一条默认 stdio 条目（根目录 ``__JACHIN_WORKSPACE__``，由 mcp_embedded_runtime 展开）。

    与 ``skills_repo/plugin/com.jachin.mcp.filesystem_workspace`` 使用相同 ``id``，
    这样 ``register_l3_packaged_stdio_mcps`` 的 ``add_server`` 会因已连接而跳过，不会重复挂载工具。

    Returns:
        是否写回了磁盘。
    """
    jachin_dir = Path.home() / ".jachin"
    try:
        jachin_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.debug("[mcp_json_repair] 无法创建 ~/.jachin: %s", e)
        return False

    cfg_path = jachin_dir / "mcp_servers.json"
    blob: dict[str, Any] | list[Any]

    entry: dict[str, Any] = {
        "id": _DEFAULT_FILESYSTEM_MCP_ID,
        "name": "MCP Filesystem（官方 · 工作区）",
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "__JACHIN_WORKSPACE__"],
    }

    if not cfg_path.is_file():
        blob = {"mcp_servers": [entry]}
        try:
            cfg_path.write_text(json.dumps(blob, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except OSError as e:
            logger.warning("[mcp_json_repair] 写入默认 Filesystem MCP 失败: %s", e)
            return False
        logger.info(
            "[mcp_json_repair] 已创建 %s 并写入默认官方 Filesystem MCP（需本机 Node/npx）",
            cfg_path,
        )
        return True

    try:
        parsed = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError) as e:
        logger.debug("[mcp_json_repair] 跳过默认 Filesystem：读取失败 %s", e)
        return False

    if isinstance(parsed, dict):
        if not isinstance(parsed.get("mcp_servers"), list):
            parsed["mcp_servers"] = []
        servers = parsed["mcp_servers"]
        blob = parsed
    elif isinstance(parsed, list):
        servers = parsed
        blob = parsed
    else:
        return False

    for e in servers:
        if isinstance(e, dict) and _mcp_entry_has_official_filesystem(e):
            return False

    servers.append(entry)
    try:
        cfg_path.write_text(json.dumps(blob, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as e:
        logger.warning("[mcp_json_repair] 合并默认 Filesystem MCP 失败: %s", e)
        return False
    logger.info(
        "[mcp_json_repair] 已向 %s 追加默认官方 Filesystem MCP（需本机 Node/npx）",
        cfg_path,
    )
    return True


def _mcp_entry_has_official_fetch_stdio(entry: dict[str, Any]) -> bool:
    """已配置基于 ``mcp_server_fetch`` 的 stdio（与仓库 tools/mcp-official 示例一致即可）。"""
    args = entry.get("args")
    if not isinstance(args, list):
        return False
    return any(isinstance(a, str) and "mcp_server_fetch" in a for a in args)


def _fetch_ignore_robots_enabled() -> bool:
    v = (os.environ.get("JACHIN_MCP_FETCH_IGNORE_ROBOTS") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _default_official_fetch_args() -> list[str]:
    out = ["-m", "mcp_server_fetch"]
    if _fetch_ignore_robots_enabled():
        out.append("--ignore-robots-txt")
    return out


def repair_official_fetch_ignore_robots_arg() -> bool:
    """
    当 ``JACHIN_MCP_FETCH_IGNORE_ROBOTS`` 为真时，为所有含 ``mcp_server_fetch`` 的 stdio 条目
    追加 ``--ignore-robots-txt``（若尚未存在）。

    Returns:
        是否写回了磁盘。
    """
    if not _fetch_ignore_robots_enabled():
        return False

    cfg_path = Path.home() / ".jachin" / "mcp_servers.json"
    if not cfg_path.is_file():
        return False

    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError) as e:
        logger.debug("[mcp_json_repair] 跳过 Fetch robots 修补：读取失败 %s", e)
        return False

    changed = False

    def _patch_one(entry: dict[str, Any]) -> None:
        nonlocal changed
        if not _mcp_entry_has_official_fetch_stdio(entry):
            return
        args = entry.get("args")
        if not isinstance(args, list):
            return
        if any(isinstance(a, str) and a.strip() == "--ignore-robots-txt" for a in args):
            return
        args.append("--ignore-robots-txt")
        changed = True
        sid = str(entry.get("id") or entry.get("name") or "").strip() or "?"
        logger.info(
            "[mcp_json_repair] 已为 Fetch MCP（id=%s）追加 --ignore-robots-txt（JACHIN_MCP_FETCH_IGNORE_ROBOTS）",
            sid,
        )

    if isinstance(data, dict) and isinstance(data.get("mcp_servers"), list):
        for e in data["mcp_servers"]:
            if isinstance(e, dict):
                _patch_one(e)
    elif isinstance(data, list):
        for e in data:
            if isinstance(e, dict):
                _patch_one(e)

    if not changed:
        return False

    try:
        cfg_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as e:
        logger.warning("[mcp_json_repair] Fetch robots 修补写入失败: %s", e)
        return False
    return True


def ensure_default_official_fetch_mcp() -> bool:
    """
    若 ``~/.jachin/mcp_servers.json`` 中尚无 ``-m mcp_server_fetch`` 条目，则追加一条默认 stdio。

    使用 ``__JACHIN_MCP_PYTHON__``（与嵌入式/当前 Python 一致），工具在模型侧多为 **mcp:fetch** / ``fetch``。
    需 ``pip install mcp-server-fetch``（亦在 ``tools/mcp-official/requirements-official-mcp.txt``）。

    Returns:
        是否写回了磁盘。
    """
    jachin_dir = Path.home() / ".jachin"
    try:
        jachin_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.debug("[mcp_json_repair] 无法创建 ~/.jachin（fetch）: %s", e)
        return False

    cfg_path = jachin_dir / "mcp_servers.json"
    entry: dict[str, Any] = {
        "id": _DEFAULT_FETCH_MCP_ID,
        "name": "MCP Fetch（官方 mcp-server-fetch，单 URL→Markdown）",
        "command": "__JACHIN_MCP_PYTHON__",
        "args": _default_official_fetch_args(),
        "env": {"PYTHONIOENCODING": "utf-8"},
    }

    if not cfg_path.is_file():
        blob: dict[str, Any] = {"mcp_servers": [entry]}
        try:
            cfg_path.write_text(json.dumps(blob, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except OSError as e:
            logger.warning("[mcp_json_repair] 写入默认 Fetch MCP 失败: %s", e)
            return False
        logger.info(
            "[mcp_json_repair] 已创建 %s 并写入默认官方 Fetch MCP（需 pip install mcp-server-fetch）",
            cfg_path,
        )
        return True

    try:
        parsed = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError) as e:
        logger.debug("[mcp_json_repair] 跳过默认 Fetch：读取失败 %s", e)
        return False

    if isinstance(parsed, dict):
        if not isinstance(parsed.get("mcp_servers"), list):
            parsed["mcp_servers"] = []
        servers = parsed["mcp_servers"]
        blob = parsed
    elif isinstance(parsed, list):
        servers = parsed
        blob = parsed
    else:
        return False

    for e in servers:
        if isinstance(e, dict) and _mcp_entry_has_official_fetch_stdio(e):
            return False

    servers.append(entry)
    try:
        cfg_path.write_text(json.dumps(blob, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as e:
        logger.warning("[mcp_json_repair] 合并默认 Fetch MCP 失败: %s", e)
        return False
    logger.info(
        "[mcp_json_repair] 已向 %s 追加默认官方 Fetch MCP（需 pip install mcp-server-fetch）",
        cfg_path,
    )
    return True


def _is_hr_stdio_entry(entry: dict[str, Any]) -> bool:
    sid = str(entry.get("id") or entry.get("name") or "").strip().lower()
    return sid in _HR_SERVER_IDS_LOWER


def _hr_first_py_arg_should_repoint(first: str, hr_server: Path) -> bool:
    """旧仓库路径、已废弃的 2-track 包、或非规范 HR server.py 时重写为 hr_server。"""
    if not isinstance(first, str) or not first.strip():
        return True
    if not first.lower().endswith(".py"):
        return True
    norm = first.replace("\\", "/").lower()
    if "2-track-a-atomic-mcp" in norm:
        return True
    # 常见误配：目录名为 jachin-system（缺 -main）而本仓为 jachin-system-main
    segs = [p for p in norm.split("/") if p]
    if "jachin-system" in segs and "jachin-system-main" not in segs:
        return True
    p = Path(first)
    try:
        if not p.is_file():
            return True
    except OSError:
        return True
    try:
        if hr_server.is_file() and p.resolve() != hr_server.resolve():
            return True
    except OSError:
        return True
    return False


def repair_hr_atomic_tools_path(project_root: Path) -> bool:
    """
    若存在 id=hr-atomic-tools 且 args 中 .py 文件不存在，则改为 project_root 下
    skills_repo/plugin/com.jachin.hr.recruitment/server.py。

    Returns:
        是否写回了磁盘（写回后应让 MCPManager 重新读配置）。
    """
    hr_server = (
        project_root.resolve() / "skills_repo" / "plugin" / "com.jachin.hr.recruitment" / "server.py"
    )
    if not hr_server.is_file():
        return False

    cfg_path = Path.home() / ".jachin" / "mcp_servers.json"
    if not cfg_path.is_file():
        return False

    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError) as e:
        logger.debug("[mcp_json_repair] 跳过: 读取失败 %s", e)
        return False

    # 与当前进程一致，避免 PATH 上另有 python 导致子环境与 L3 不一致
    py_exe = sys.executable or shutil.which("python") or shutil.which("python3") or "python"
    changed = False

    def _fix_entry(entry: dict[str, Any]) -> None:
        nonlocal changed
        if not _is_hr_stdio_entry(entry):
            return
        args = entry.get("args")
        if not isinstance(args, list) or not args:
            entry["command"] = py_exe
            entry["args"] = [str(hr_server.resolve())]
            changed = True
            logger.info("[mcp_json_repair] 已修正 hr-atomic-tools（补全 args）-> %s", hr_server)
            return
        first = args[0]
        if not isinstance(first, str) or _hr_first_py_arg_should_repoint(first, hr_server):
            entry["command"] = py_exe
            entry["args"] = [str(hr_server.resolve())]
            changed = True
            logger.info(
                "[mcp_json_repair] 已修正 hr-atomic-tools 脚本路径 -> %s",
                hr_server,
            )

    if isinstance(data, dict) and isinstance(data.get("mcp_servers"), list):
        for e in data["mcp_servers"]:
            if isinstance(e, dict):
                _fix_entry(e)
    elif isinstance(data, list):
        # Cursor / 部分导出为顶层数组，MCPManager._load_config 支持，repair 也必须遍历
        for e in data:
            if isinstance(e, dict):
                _fix_entry(e)
    elif isinstance(data, dict) and isinstance(data.get("mcpServers"), dict):
        inner = data["mcpServers"].get("hr-atomic-tools")
        if isinstance(inner, dict):
            args = inner.get("args")
            if not isinstance(args, list) or not args:
                inner["command"] = py_exe
                inner["args"] = [str(hr_server.resolve())]
                changed = True
                logger.info(
                    "[mcp_json_repair] 已修正 mcpServers.hr-atomic-tools（补全 args）-> %s",
                    hr_server,
                )
            else:
                first = args[0]
                if not isinstance(first, str) or _hr_first_py_arg_should_repoint(first, hr_server):
                    inner["command"] = py_exe
                    inner["args"] = [str(hr_server.resolve())]
                    changed = True
                    logger.info(
                        "[mcp_json_repair] 已修正 mcpServers.hr-atomic-tools -> %s",
                        hr_server,
                    )

    if not changed:
        return False

    try:
        cfg_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as e:
        logger.warning("[mcp_json_repair] 写入失败: %s", e)
        return False
    return True


_SQLITE_MANAGER_ID = "sqlite_manager"


def _mcp_entry_has_sqlite_manager_uvx(entry: dict[str, Any]) -> bool:
    """已配置 sqlite_manager 或基于 PyPI mcp-server-sqlite 的 uvx 条目。"""
    if str(entry.get("id") or "").strip() == _SQLITE_MANAGER_ID:
        return True
    args = entry.get("args")
    if not isinstance(args, list):
        return False
    return any(isinstance(a, str) and "mcp-server-sqlite" in a for a in args)


def ensure_sqlite_manager_life_db_mcp() -> bool:
    """
    若 ``~/.jachin/mcp_servers.json`` 中尚无 ``sqlite_manager`` / ``mcp-server-sqlite`` 条目，
    则追加默认 **uvx mcp-server-sqlite**（数据库 ``__JACHIN_WORKSPACE__/my_life_data.db``）。

    与 ``ensure_jachin_workspace_my_life_sqlite_db`` 配合：先占位库文件再拉起 MCP。
    需本机安装 `uv` 且 ``uvx`` 在 PATH 中。

    Returns:
        是否写回了磁盘。
    """
    if (os.environ.get("JACHIN_MCP_NO_AUTO_SQLITE_MANAGER") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return False

    jachin_dir = Path.home() / ".jachin"
    try:
        jachin_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.debug("[mcp_json_repair] 无法创建 ~/.jachin: %s", e)
        return False

    cfg_path = jachin_dir / "mcp_servers.json"
    entry: dict[str, Any] = {
        "id": _SQLITE_MANAGER_ID,
        "name": "SQLite 生活库 / 记账（PyPI mcp-server-sqlite，uvx）",
        "command": "uvx",
        "args": [
            "mcp-server-sqlite",
            "--db-path",
            "__JACHIN_WORKSPACE__/my_life_data.db",
        ],
    }

    if not cfg_path.is_file():
        blob: dict[str, Any] = {"mcp_servers": [entry]}
        try:
            cfg_path.write_text(json.dumps(blob, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except OSError as e:
            logger.warning("[mcp_json_repair] 写入默认 sqlite_manager MCP 失败: %s", e)
            return False
        logger.info(
            "[mcp_json_repair] 已创建 %s 并写入默认 sqlite_manager（需 uvx）",
            cfg_path,
        )
        return True

    try:
        parsed = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError) as e:
        logger.debug("[mcp_json_repair] 跳过 sqlite_manager：读取失败 %s", e)
        return False

    if isinstance(parsed, dict):
        if not isinstance(parsed.get("mcp_servers"), list):
            parsed["mcp_servers"] = []
        servers = parsed["mcp_servers"]
        blob = parsed
    elif isinstance(parsed, list):
        servers = parsed
        blob = parsed
    else:
        return False

    for e in servers:
        if isinstance(e, dict) and _mcp_entry_has_sqlite_manager_uvx(e):
            return False

    servers.append(entry)
    try:
        cfg_path.write_text(json.dumps(blob, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as e:
        logger.warning("[mcp_json_repair] 合并 sqlite_manager MCP 失败: %s", e)
        return False
    logger.info(
        "[mcp_json_repair] 已向 %s 追加默认 sqlite_manager（uvx mcp-server-sqlite）",
        cfg_path,
    )
    return True
