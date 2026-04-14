"""
MCP stdio 子进程用的嵌入式 Python / Node 路径解析与预检。

目录约定（安装包或用户目录，版本见 manifest.example.json）::

    <JACHIN_HOME>/runtime/
      manifest.json          # 可选，记录捆绑版本
      python/python.exe      # Windows embeddable
      python/bin/python3     # Unix
      node/node.exe          # Windows portable
      node/bin/node          # Unix

优先顺序：环境变量 JACHIN_MCP_PYTHON / JACHIN_MCP_NODE / JACHIN_MCP_NPX →
便携包 JACHIN_APP_ROOT/runtime → ~/.jachin/runtime →
frozen 下 exe 旁 runtime/ → 系统 PATH（python/python3/node/npx）。

占位符（command / args / env 字符串）：__JACHIN_MCP_PYTHON__、__JACHIN_MCP_NODE__、__JACHIN_MCP_NPX__、__JACHIN_WORKSPACE__
（后者展开为 ``~/.jachin/workspace`` 或 ``$JACHIN_HOME/workspace`` 的绝对路径，供 MCP 如 server-sqlite 的 ``--db-path``）

**npx**：官方 MCP 常用 ``command: npx``；若已将 Node 便携包解压到 ``runtime/node/``（含 ``npx.cmd`` / ``npx``），
则裸 ``npx`` / ``npm`` 会解析到该路径，无需系统安装 Node。见 ``tools/mcp-runtime/README.txt``。

env 值中可使用 ``${VAR_NAME}``，在拉起子进程前从 **当前进程** ``os.environ`` 展开（便于密钥只放在 .env / 系统环境，不进 JSON）。
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

TOKEN_PYTHON = "__JACHIN_MCP_PYTHON__"
TOKEN_NODE = "__JACHIN_MCP_NODE__"
TOKEN_NPX = "__JACHIN_MCP_NPX__"
TOKEN_WORKSPACE = "__JACHIN_WORKSPACE__"


def _jachin_home() -> Path:
    h = os.environ.get("JACHIN_HOME")
    if h:
        return Path(h).expanduser().resolve()
    return Path.home() / ".jachin"


def _runtime_base_dirs() -> list[Path]:
    """候选 runtime 根目录（内含 python/ / node/）。"""
    roots: list[Path] = []
    seen: set[str] = set()

    def add(p: Path) -> None:
        try:
            r = p.resolve()
        except OSError:
            return
        key = str(r)
        if key not in seen:
            seen.add(key)
            roots.append(r)

    app = os.environ.get("JACHIN_APP_ROOT")
    if app:
        add(Path(app) / "runtime")

    add(_jachin_home() / "runtime")

    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        add(exe_dir / "runtime")
        add(exe_dir.parent / "runtime")

    return roots


def _first_existing(candidates: list[Path]) -> Optional[Path]:
    for p in candidates:
        try:
            if p.is_file():
                return p
        except OSError:
            continue
    return None


def _embedded_python_candidates() -> list[Path]:
    out: list[Path] = []
    for base in _runtime_base_dirs():
        out.extend(
            [
                base / "python" / "python.exe",
                base / "python" / "python3.exe",
                base / "python" / "bin" / "python3",
                base / "python" / "bin" / "python",
                base / "python" / "python3",
            ]
        )
    return out


def _embedded_node_candidates() -> list[Path]:
    out: list[Path] = []
    for base in _runtime_base_dirs():
        out.extend(
            [
                base / "node" / "node.exe",
                base / "node" / "bin" / "node",
                base / "node" / "node",
            ]
        )
    return out


def _embedded_npx_candidates() -> list[Path]:
    """官方 Windows Node zip：node.exe 与 npx.cmd / npm.cmd 同目录。"""
    out: list[Path] = []
    for base in _runtime_base_dirs():
        nd = base / "node"
        out.extend(
            [
                nd / "npx.cmd",
                nd / "npx.exe",
                nd / "npx",
                nd / "bin" / "npx",
            ]
        )
    return out


def _embedded_npm_candidates() -> list[Path]:
    out: list[Path] = []
    for base in _runtime_base_dirs():
        nd = base / "node"
        out.extend(
            [
                nd / "npm.cmd",
                nd / "npm.exe",
                nd / "npm",
                nd / "bin" / "npm",
            ]
        )
    return out


def find_embedded_python() -> Optional[Path]:
    """返回嵌入式 python 可执行文件路径，不存在则 None。"""
    env_p = (os.environ.get("JACHIN_MCP_PYTHON") or "").strip()
    if env_p:
        p = Path(env_p)
        if p.is_file():
            return p
        logger.debug("[MCP Runtime] JACHIN_MCP_PYTHON 指向的文件不存在: %s", env_p)
    return _first_existing(_embedded_python_candidates())


def find_embedded_node() -> Optional[Path]:
    env_p = (os.environ.get("JACHIN_MCP_NODE") or "").strip()
    if env_p:
        p = Path(env_p)
        if p.is_file():
            return p
        logger.debug("[MCP Runtime] JACHIN_MCP_NODE 指向的文件不存在: %s", env_p)
    return _first_existing(_embedded_node_candidates())


def find_embedded_npx() -> Optional[Path]:
    """返回嵌入式 npx 可执行文件（Windows 多为 npx.cmd），不存在则 None。"""
    env_p = (os.environ.get("JACHIN_MCP_NPX") or "").strip()
    if env_p:
        p = Path(env_p)
        if p.is_file():
            return p
        logger.debug("[MCP Runtime] JACHIN_MCP_NPX 指向的文件不存在: %s", env_p)
    return _first_existing(_embedded_npx_candidates())


def find_embedded_npm() -> Optional[Path]:
    env_p = (os.environ.get("JACHIN_MCP_NPM") or "").strip()
    if env_p:
        p = Path(env_p)
        if p.is_file():
            return p
        logger.debug("[MCP Runtime] JACHIN_MCP_NPM 指向的文件不存在: %s", env_p)
    return _first_existing(_embedded_npm_candidates())


def get_effective_mcp_python_command() -> str:
    """占位符展开用：嵌入式优先，否则 python3 / python（供 PATH 解析）。"""
    emb = find_embedded_python()
    if emb:
        return str(emb)
    if sys.platform == "win32":
        w = shutil.which("python")
        return w or "python"
    w = shutil.which("python3") or shutil.which("python")
    return w or "python3"


def get_effective_mcp_node_command() -> str:
    emb = find_embedded_node()
    if emb:
        return str(emb)
    w = shutil.which("node")
    return w or "node"


def get_effective_mcp_npx_command() -> str:
    emb = find_embedded_npx()
    if emb:
        return str(emb)
    if sys.platform == "win32":
        w = shutil.which("npx") or shutil.which("npx.cmd")
        return w or "npx"
    w = shutil.which("npx")
    return w or "npx"


def get_effective_mcp_npm_command() -> str:
    emb = find_embedded_npm()
    if emb:
        return str(emb)
    if sys.platform == "win32":
        w = shutil.which("npm") or shutil.which("npm.cmd")
        return w or "npm"
    w = shutil.which("npm")
    return w or "npm"


_ENV_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def inject_os_env_tokens(s: str) -> str:
    """将 ``${VAR}`` 替换为 os.environ.get(VAR, '')（未设置则为空串）。"""
    if not isinstance(s, str) or "${" not in s:
        return s

    def _repl(m: re.Match[str]) -> str:
        return os.environ.get(m.group(1), "")

    return _ENV_REF_RE.sub(_repl, s)


def inject_embedded_tokens(s: str) -> str:
    """将字符串中的 MCP 运行时占位符替换为实际路径或回退命令。"""
    if not isinstance(s, str) or not s:
        return s
    out = s
    if TOKEN_PYTHON in out:
        out = out.replace(TOKEN_PYTHON, get_effective_mcp_python_command())
    if TOKEN_NODE in out:
        out = out.replace(TOKEN_NODE, get_effective_mcp_node_command())
    if TOKEN_NPX in out:
        out = out.replace(TOKEN_NPX, get_effective_mcp_npx_command())
    if TOKEN_WORKSPACE in out:
        ws_path = _jachin_home() / "workspace"
        try:
            ws_path.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        ws = str(ws_path.resolve())
        out = out.replace(TOKEN_WORKSPACE, ws)
    return out


def _bare_cmd_name(command: str) -> str:
    """command 的文件名小写，用于识别裸 npx / npx.cmd。"""
    s = (command or "").strip()
    if not s:
        return ""
    try:
        return Path(s).name.lower()
    except OSError:
        return s.lower()


def _is_relative_stdio_command(cmd: str) -> bool:
    """仅对裸命令名（无盘符/非绝对路径）做嵌入式替换，避免覆盖用户显式绝对路径。"""
    s = (cmd or "").strip()
    if not s:
        return False
    try:
        p = Path(s)
        return not p.is_absolute()
    except OSError:
        return True


def resolve_mcp_stdio_command(command: str) -> str:
    """
    解析 stdio MCP 的 command：
    1) 注入 __JACHIN_MCP_PYTHON__ / __JACHIN_MCP_NODE__ / __JACHIN_MCP_NPX__
    2) 若 command 为裸 python/python3 且已部署嵌入式 Python，改用嵌入式路径
    3) 若 command 为裸 node 且已部署嵌入式 Node，改用嵌入式路径
    4) 若 command 为裸 npx / npm（及 Windows 下 npx.cmd / npm.cmd）且 runtime/node 下存在对应文件，改用嵌入式路径
    """
    cmd = inject_embedded_tokens((command or "").strip())
    if not cmd:
        return cmd
    low = cmd.lower()
    base = _bare_cmd_name(cmd)
    emb_py = find_embedded_python()
    if emb_py and low in ("python", "python3") and _is_relative_stdio_command(cmd):
        return str(emb_py)
    emb_n = find_embedded_node()
    if emb_n and low == "node" and _is_relative_stdio_command(cmd):
        return str(emb_n)
    rel = _is_relative_stdio_command(cmd)
    emb_npx = find_embedded_npx()
    if emb_npx and rel and (base in ("npx", "npx.cmd", "npx.exe") or low == "npx"):
        return str(emb_npx)
    emb_npm = find_embedded_npm()
    if emb_npm and rel and (base in ("npm", "npm.cmd", "npm.exe") or low == "npm"):
        return str(emb_npm)
    return cmd


def preflight_mcp_stdio_command(command: str, server_id: str) -> tuple[bool, str]:
    """
    预检 command 是否可在本机执行。
    返回 (ok, message)；message 仅在 ok=False 时为用户可读说明。
    """
    cmd = (command or "").strip()
    if not cmd:
        return False, f"[MCP Runtime] server_id={server_id!r} 的 command 为空。"

    p = Path(cmd)
    if p.is_file():
        return True, ""

    # 含路径分隔符时按路径处理（可能含空格等，未展开为绝对路径）
    if "/" in cmd or "\\" in cmd or (len(cmd) > 1 and cmd[1] == ":" and sys.platform == "win32"):
        if p.is_file():
            return True, ""
        hint = (
            f"[MCP Runtime] 未找到可执行文件: {cmd!r}（server_id={server_id}）。"
            "请将嵌入式 Python 解压到 ~/.jachin/runtime/python/ 或便携包 runtime/python/，"
            "或设置环境变量 JACHIN_MCP_PYTHON 为 python.exe 绝对路径。"
            " 布局与版本说明见 tools/mcp-runtime/README.txt"
        )
        return False, hint

    found = shutil.which(cmd)
    if found:
        return True, ""

    hint = (
        f"[MCP Runtime] 无法在 PATH 中找到 {cmd!r}（server_id={server_id}）。"
        "请安装系统 Python/Node/npx，或将嵌入式运行时放入 ~/.jachin/runtime/（python、node 子目录，"
        "Node 便携包须含 node.exe 与 npx.cmd），"
        "或设置 JACHIN_MCP_PYTHON / JACHIN_MCP_NODE / JACHIN_MCP_NPX。"
        " 详见 tools/mcp-runtime/README.txt"
    )
    return False, hint


def mask_secret_for_log(val: str | None) -> str:
    """日志脱敏：不输出完整 API Key。"""
    v = (val or "").strip()
    if not v:
        return "(空)"
    if len(v) <= 8:
        return f"(已设置 len={len(v)})"
    return f"{v[:4]}...{v[-4:]} len={len(v)}"


def _tavily_chain_logging_disabled() -> bool:
    """设为 ``JACHIN_LOG_TAVILY_CHAIN=0`` / ``false`` / ``no`` 时关闭整条 Tavily 排障日志（默认开启）。"""
    return (os.environ.get("JACHIN_LOG_TAVILY_CHAIN") or "1").strip().lower() in ("0", "false", "no")


def _tavily_key_diag(parent_has: bool, stdio_has: bool) -> str:
    """单行可检索结论，便于 grep ``[TavilyMCP]``。"""
    if parent_has and stdio_has:
        return "父进程与子进程env均含TAVILY(传参路径正常)"
    if parent_has and not stdio_has:
        return "警告-父有Key但stdio.env未带Key(查resolve_placeholders/实例.env)"
    if not parent_has and stdio_has:
        return "仅stdio有Key父进程无(罕见)"
    return "两端均无TAVILY_KEY(不可用)"


def log_tavily_mcp_chain(
    phase: str,
    server_id: str,
    stdio_env: dict[str, Any] | None,
    *,
    log_parent_os: bool = False,
) -> None:
    """
    Tavily stdio 关键路径：MCP Python SDK 不会把 L3 全量环境传给子进程，只合并白名单 + ``stdio_env``。
    同时打出 ``parent_os_has_TAVILY`` / ``stdio_env_has_TAVILY`` 与 ``diag=``，便于对照是否「传不过去」。
    关闭：环境变量 ``JACHIN_LOG_TAVILY_CHAIN=0``。
    """
    if _tavily_chain_logging_disabled():
        return
    sid = str(server_id or "unknown").strip()
    par = (os.environ.get("TAVILY_API_KEY") or "").strip()
    par_has = bool(par)
    sto = ""
    if isinstance(stdio_env, dict):
        sto = str(stdio_env.get("TAVILY_API_KEY") or "").strip()
    stdio_has = bool(sto)
    extra = ""
    if log_parent_os:
        extra = f" parent_key_masked={mask_secret_for_log(par)}"
    logger.info(
        "[TavilyMCP][chain] phase=%s server_id=%s parent_os_has_TAVILY=%s stdio_env_has_TAVILY=%s "
        "stdio_key_masked=%s%s diag=%s",
        phase,
        sid,
        par_has,
        stdio_has,
        mask_secret_for_log(sto),
        extra,
        _tavily_key_diag(par_has, stdio_has),
    )


def log_tavily_invoke_outcome(tool_name: str, result: str | None) -> None:
    """
    工具调用返回后一行摘要：成功只打长度；疑似错误打 WARNING + 截断预览（仍勿贴完整用户查询内容外的密钥）。
    """
    if _tavily_chain_logging_disabled():
        return
    tn = (tool_name or "").strip()
    if "tavily" not in tn.lower():
        return
    r = (result or "").strip()
    low = r.lower()
    # 避免把网页正文里的 "error" 误判为失败；偏 JSON-RPC / 鉴权类文案
    bad = any(
        x in low
        for x in (
            "-32600",
            "-32602",
            "-32603",
            "environment variable is required",
            "missing tavily",
            "invalid api key",
            "api key not",
            "unauthorized",
            "authentication failed",
            "incorrect api key",
            "quota exceeded",
            "rate limit",
        )
    ) or ("[mcp" in low and "error" in low)
    if bad and len(r) < 800:
        logger.warning("[TavilyMCP][invoke] tool=%s outcome=likely_error len=%s body=%s", tn, len(r), r)
    elif bad:
        logger.warning(
            "[TavilyMCP][invoke] tool=%s outcome=likely_error len=%s body_preview=%s…",
            tn,
            len(r),
            r[:400],
        )
    else:
        logger.info("[TavilyMCP][invoke] tool=%s outcome=ok len=%s", tn, len(r))


def _is_tavily_stdio_cfg(out: dict[str, Any]) -> bool:
    """是否 Tavily 官方 npx stdio（需在 env 中显式带 TAVILY_API_KEY）。"""
    sid = str(out.get("id") or out.get("name") or "").lower()
    if "tavily" in sid:
        return True
    args = out.get("args")
    if isinstance(args, list):
        for a in args:
            if isinstance(a, str) and "tavily" in a.lower():
                return True
    return False


def is_tavily_stdio_server(server_id: str, args: Any) -> bool:
    """与 ``_is_tavily_stdio_cfg`` 一致，仅 id + args（供 connect 时判断）。"""
    return _is_tavily_stdio_cfg({"id": server_id, "args": args if isinstance(args, list) else []})


def _is_google_maps_stdio_cfg(out: dict[str, Any]) -> bool:
    """官方 npm ``@modelcontextprotocol/server-google-maps``（出行/地理/路线）。"""
    sid = str(out.get("id") or out.get("name") or "").lower()
    if sid == "maps_assistant" or ("google" in sid and "map" in sid):
        return True
    args = out.get("args")
    if isinstance(args, list):
        for a in args:
            if isinstance(a, str) and "server-google-maps" in a:
                return True
    return False


def is_google_maps_stdio_server(server_id: str, args: Any) -> bool:
    return _is_google_maps_stdio_cfg({"id": server_id, "args": args if isinstance(args, list) else []})


def _google_maps_api_key_from_os() -> str:
    """npm 包读 ``GOOGLE_MAPS_API_KEY``；兼容用户别名 ``Maps_API_KEY`` / ``MAPS_API_KEY``。"""
    k = (os.environ.get("GOOGLE_MAPS_API_KEY") or "").strip()
    if k:
        return k
    return (os.environ.get("Maps_API_KEY") or os.environ.get("MAPS_API_KEY") or "").strip()


def effective_stdio_env_for_sdk(
    server_id: str,
    args: Any,
    raw_env: dict[str, Any] | None,
) -> dict[str, str] | None:
    """
    生成传给 ``mcp.StdioServerParameters.env`` 的值。

    **关键（与官方 MCP Python SDK 一致）**：``stdio_client`` 在 ``env is None`` 时子进程**仅有**
    ``get_default_environment()`` 白名单（**不含** ``TAVILY_API_KEY``）；只有 ``env`` 为非空 dict 时才会
    合并 ``{**get_default_environment(), **env}``。因此 Tavily **禁止**传 ``env=None``，否则 Node 进程内
    ``process.env.TAVILY_API_KEY`` 为空，``list_tools`` 可能仍成功，**首次** ``tavily_search`` 才报
    ``-32600 TAVILY_API_KEY environment variable is required``。

    对 Tavily / Google Maps（npx Node）：须向子进程显式传入 API Key，否则进程内读不到（与 Tavily 同理）。
    非上述：无显式变量时返回 ``None`` 以保持与旧行为一致。
    """
    out: dict[str, str] = {}
    if isinstance(raw_env, dict):
        for k, v in raw_env.items():
            if v is None:
                continue
            out[str(k)] = str(v)
    tavily = is_tavily_stdio_server(server_id, args)
    if tavily:
        tv = (os.environ.get("TAVILY_API_KEY") or "").strip()
        if tv and not (out.get("TAVILY_API_KEY") or "").strip():
            out["TAVILY_API_KEY"] = tv
        if not (out.get("TAVILY_API_KEY") or "").strip():
            logger.warning(
                "[TavilyMCP] effective_stdio_env: 父进程无 TAVILY_API_KEY，Tavily 工具调用将报 -32600（请配置 .env）"
            )
        return out
    gmaps = is_google_maps_stdio_server(server_id, args)
    if gmaps:
        gm = _google_maps_api_key_from_os()
        if gm and not (out.get("GOOGLE_MAPS_API_KEY") or "").strip():
            out["GOOGLE_MAPS_API_KEY"] = gm
        if not (out.get("GOOGLE_MAPS_API_KEY") or "").strip():
            logger.warning(
                "[GoogleMapsMCP] effective_stdio_env: 父进程无 GOOGLE_MAPS_API_KEY（可在 .env 设 Maps_API_KEY 别名），地图工具将不可用"
            )
        return out
    return out if out else None


def resolve_tavily_stdio_cwd() -> Optional[str]:
    """
    tavily-mcp 入口执行 ``dotenv.config()``（默认读取 **当前工作目录** 下 ``.env``），随后
    ``const API_KEY = process.env.TAVILY_API_KEY``（见 npm ``tavily-mcp`` 的 ``src/index.ts``）。

    若用户在 ``clients/desktop`` 等子目录启动 ``python -m l3_node``，stdio 子进程 **cwd** 常为该子目录，
    而 ``TAVILY_API_KEY`` 仅在仓库根 ``.env``，则 Node 侧 ``API_KEY`` 为空 → 首次 ``tavily_search`` 报
    ``-32600 TAVILY_API_KEY environment variable is required``，与 Python 侧已 merge 密钥并存。

    将子进程 ``cwd`` 设为「已存在 ``.env``」的目录，优先与 ``merge_l3_dotenv_into_os`` 可能加载的路径一致。
    """
    candidates: list[Path] = []
    ja = (os.environ.get("JACHIN_APP_ROOT") or "").strip()
    if ja:
        try:
            candidates.append(Path(ja).expanduser().resolve())
        except OSError:
            pass
    try:
        repo = Path(__file__).resolve().parent.parent
        candidates.append(repo)
    except OSError:
        pass
    try:
        candidates.append(Path.cwd().resolve())
    except OSError:
        pass
    candidates.append(Path.home() / ".jachin")
    seen: set[str] = set()
    for base in candidates:
        if not base:
            continue
        try:
            key = str(base.resolve())
        except OSError:
            key = str(base)
        if key in seen:
            continue
        seen.add(key)
        try:
            if (base / ".env").is_file():
                return str(base.resolve())
        except OSError:
            continue
    cwd = Path.cwd().resolve()
    for _ in range(8):
        try:
            if (cwd / ".env").is_file():
                return str(cwd)
        except OSError:
            pass
        par = cwd.parent
        if par == cwd:
            break
        cwd = par
    return None


def log_tavily_stdio_cwd_choice(server_id: str, cwd: Optional[str]) -> None:
    """可检索：Tavily stdio 子进程 cwd（与 dotenv.config 对齐）。"""
    if _tavily_chain_logging_disabled():
        return
    logger.info(
        "[TavilyMCP][chain] phase=stdio_cwd_for_dotenv server_id=%s cwd=%s",
        server_id,
        cwd or "(default_inherit)",
    )


def expand_stdio_env_windows_npx_tavily(
    server_id: str,
    args: Any,
    eff_env: dict[str, str] | None,
) -> dict[str, str] | None:
    """
    Windows 专用：``npx.cmd`` → Node 的链式子进程在部分环境下，仅靠
    ``{**get_default_environment(), **显式小 dict}`` 仍可能让 Tavily 包内读不到
    ``process.env.TAVILY_API_KEY``（list_tools 仍成功，首次 search 才 -32600）。

    在已通过 ``effective_stdio_env_for_sdk`` 得到显式 Key 的前提下，将 **完整**
    ``os.environ``（全部转为 str）作为基底，再用 ``eff_env`` 覆盖，使行为接近
    「在父 shell 已 export 后启动 npx」。
    """
    if sys.platform != "win32":
        return eff_env
    if not is_tavily_stdio_server(server_id, args):
        return eff_env
    base: dict[str, str] = {}
    for k, v in os.environ.items():
        if v is None:
            continue
        try:
            base[str(k)] = str(v)
        except Exception:
            continue
    if eff_env:
        base.update(eff_env)
    if not _tavily_chain_logging_disabled():
        tv = str(base.get("TAVILY_API_KEY") or "").strip()
        logger.info(
            "[TavilyMCP][chain] phase=stdio_win32_full_parent_env server_id=%s keys=%s has_TAVILY=%s key_masked=%s",
            server_id,
            len(base),
            bool(tv),
            mask_secret_for_log(tv),
        )
    return base


def expand_stdio_env_windows_npx_google_maps(
    server_id: str,
    args: Any,
    eff_env: dict[str, str] | None,
) -> dict[str, str] | None:
    """Windows：npx 链式子进程与 Tavily 类似，合并完整父环境以确保 ``GOOGLE_MAPS_API_KEY`` 可见。"""
    if sys.platform != "win32":
        return eff_env
    if not is_google_maps_stdio_server(server_id, args):
        return eff_env
    base: dict[str, str] = {}
    for k, v in os.environ.items():
        if v is None:
            continue
        try:
            base[str(k)] = str(v)
        except Exception:
            continue
    if eff_env:
        base.update(eff_env)
    return base


def log_tavily_stdio_merged_spawn(server_id: str, explicit_env: dict[str, str] | None) -> None:
    """记录 SDK 合并后子进程可见的 TAVILY（与 ``get_default_environment()`` 合并后）。"""
    if _tavily_chain_logging_disabled():
        return
    try:
        from mcp.client.stdio import get_default_environment

        merged = {**get_default_environment(), **(explicit_env or {})}
        tv = str(merged.get("TAVILY_API_KEY") or "").strip()
        logger.info(
            "[TavilyMCP][chain] phase=stdio_spawn_merged server_id=%s merged_has_TAVILY=%s merged_key_masked=%s",
            server_id,
            bool(tv),
            mask_secret_for_log(tv),
        )
    except Exception as e:
        logger.debug("[TavilyMCP] stdio_spawn_merged log skip: %s", e)


def resolve_mcp_cfg_placeholders(cfg: dict[str, Any]) -> dict[str, Any]:
    """解析 command、args、env：先 __JACHIN_*__，再 env 值中的 ``${VAR}``。

    **重要（MCP Python SDK stdio）**：子进程环境为 ``get_default_environment()``（PATH、USERPROFILE 等白名单）
    与配置 ``env`` 的合并，**不会**继承 L3 进程的全部 ``os.environ``。因此像 ``TAVILY_API_KEY`` 这类密钥
    必须出现在解析后的 ``env`` 里；展开后若仍为空，会尝试用当前 ``os.environ`` 回填；Tavily 则在检测到
    相关配置且父进程已有 Key 时自动补全 ``TAVILY_API_KEY``。

    解析前合并 ``.env``：避免未走 ``l3_node.__main__`` 或执行顺序导致占位符展开时父进程仍无 Key。
    """
    try:
        from core.l3_dotenv_merge import merge_l3_dotenv_into_os

        merge_l3_dotenv_into_os()
    except Exception:
        pass
    out = dict(cfg)
    cmd = out.get("command")
    if isinstance(cmd, str):
        out["command"] = inject_embedded_tokens(cmd.strip())
    args = out.get("args")
    if isinstance(args, list):
        out["args"] = [inject_embedded_tokens(a) if isinstance(a, str) else a for a in args]
    env = out.get("env")
    if isinstance(env, dict):
        out["env"] = {}
        for k, v in env.items():
            if isinstance(v, str):
                out["env"][str(k)] = inject_os_env_tokens(inject_embedded_tokens(v))
            else:
                out["env"][str(k)] = v
        # 占位符展开后仍为空：用父进程已加载的同名变量回填（常见于 .env 已合并但拼写/时机边缘情况）
        for k in list(out["env"].keys()):
            v = out["env"][k]
            if isinstance(v, str) and not v.strip():
                ev = (os.environ.get(k) or "").strip()
                if ev:
                    out["env"][k] = ev
    # Tavily：即使 JSON 未写 env 块，只要 L3 已具备 TAVILY_API_KEY，也注入，避免 stdio 白名单继承不到该 Key
    if _is_tavily_stdio_cfg(out):
        tv = (os.environ.get("TAVILY_API_KEY") or "").strip()
        if tv:
            if not isinstance(out.get("env"), dict):
                out["env"] = {}
            if not (str(out["env"].get("TAVILY_API_KEY") or "").strip()):
                out["env"]["TAVILY_API_KEY"] = tv
        _sid = str(out.get("id") or out.get("name") or "unknown")
        log_tavily_mcp_chain("resolve_placeholders", _sid, out.get("env") if isinstance(out.get("env"), dict) else None, log_parent_os=True)
    if _is_google_maps_stdio_cfg(out):
        if not isinstance(out.get("env"), dict):
            out["env"] = {}
        gm = _google_maps_api_key_from_os()
        if gm and not (str(out["env"].get("GOOGLE_MAPS_API_KEY") or "").strip()):
            out["env"]["GOOGLE_MAPS_API_KEY"] = gm
        if not (str(out["env"].get("GOOGLE_MAPS_API_KEY") or "").strip()):
            logger.warning(
                "[GoogleMapsMCP] 未配置 GOOGLE_MAPS_API_KEY（或别名 Maps_API_KEY）；请写入仓库或 ~/.jachin/.env"
            )
    return out


def resolve_and_preflight_command(command: str, server_id: str) -> tuple[Optional[str], Optional[str]]:
    """
    解析 command 并预检。
    返回 (resolved_command, None) 或 (None, error_message)。
    """
    resolved = resolve_mcp_stdio_command(command)
    ok, msg = preflight_mcp_stdio_command(resolved, server_id)
    if not ok:
        return None, msg
    return resolved, None


def ensure_jachin_workspace_my_life_sqlite_db() -> None:
    """
    确保 ``~/.jachin/workspace`` 存在，且 ``my_life_data.db`` 占位文件已创建，
    供 ``uvx mcp-server-sqlite --db-path …`` 首次连接（空文件即可，SQLite 首次打开时初始化）。
    """
    try:
        ws = _jachin_home() / "workspace"
        ws.mkdir(parents=True, exist_ok=True)
        db = ws / "my_life_data.db"
        if not db.exists():
            db.touch()
            logger.info("[Jachin MCP] 已创建本地生活库占位: %s", db)
    except OSError as e:
        logger.debug("[Jachin MCP] ensure my_life_data.db 跳过: %s", e)
