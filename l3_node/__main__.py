"""
L3 节点独立运行入口

用法:
  python -m l3_node --ws-only    # 仅启动 WebSocket，用 OPENAI_API_KEY 兜底

  python -m l3_node --gateway    # L2 零信任配对：向 L2 宣誓效忠，等待审批后点火

  python -m l3_node              # 需 SUB_ACCOUNT_ID，连接 L2 引导（旧模式）

环境变量:
  L2_BASE_URL: L2 地址，默认 http://localhost:18888
  SUB_ACCOUNT_ID: 子账号 ID（非 ws-only/--gateway 模式必需）
  OPENAI_API_KEY: ws-only 或 L2 无 Key 时的兜底
  JACHIN_L3_MCP_NO_L2: 1/true 时禁止一切 MCP 调用转发 L2（原子任务仅在 L3：stdio / Native 桥接）。
  JACHIN_L3_LOCAL_ONLY: 1/true 时 MCP 工具列表不合并 L2，且 invoke 禁止转发 L2（与上条「禁止 L2」叠加语义一致）。
  JACHIN_L3_CONSOLE: Windows 下是否弹出独立控制台。1/true 强制开启；0/false 强制关闭。
    未设置时：PyInstaller 打包 exe 默认开启（便于目标机看日志）；源码运行默认关闭。
  JACHIN_EXEC_TRACE_STDERR: 0/false/off 关闭 [JachinExec] 执行里程碑的 stderr 同步打印（默认开启，便于 PowerShell 排障）。
  JACHIN_L3_DEEP_LOG: 0/false/off 关闭「深度执行日志」logger（jachin.deep）：默认开启，将 ReAct/LLM 轨迹、模型输出、
    工具入参/出参、耗时等写入 PowerShell、~/.jachin/l3_debug.log 与全息 SSE（分片推送）。
  JACHIN_L3_DEEP_LOG_FULL_PAYLOAD: 1/true 时恢复「全量请求体」深度日志（完整 messages 块 + tools 名称列表长文、大段 RESPONSE text）；
    未设置则仅记录条数/各 role 长度/tools 数与名称哈希等摘要（推荐桌面/打包，避免同步写日志阻塞）。
    旧名 JACHIN_L3_DEEP_LOG_FULL_REQUEST 仍有效。
  JACHIN_L3_LOG_LITELLM_DETAIL: 默认 0；为 1 时逐条打印发往 LiteLLM 的消息结构（体积大，仅深度排障开）。
  打包后即使控制台在后台（或无控制台），子进程若仍向 stdout/stderr 管道同步写巨量日志，同样可能背压阻塞 —— 与前台终端「快速编辑暂停」不同，但现象类似（需 Ctrl+C / 减压日志）。
  JACHIN_L3_LOG_ASYNC: 默认 1，l3_debug.log 经 QueueHandler + 后台线程批量写盘；0 为同步 FileHandler。
  JACHIN_L3_LOG_ASYNC_BATCH_CHARS / JACHIN_L3_LOG_ASYNC_FLUSH_SEC: 批量写与刷新间隔（见 l3_node/async_file_log.py）。
  LOG_LEVEL: 根 logger 级别（默认 INFO）。设为 WARNING 时可压低第三方库噪音。
  JACHIN_LOG_LEVEL: 可选，显式指定 Jachin 自有 logger（l3_node、core）级别；未设置且 LOG_LEVEL≥WARNING 时默认 INFO，
    以便控制台与 l3_debug.log 仍能看到 [L3 Agent] 等 INFO 行。
  JACHIN_REACT_STREAM_DISABLE_TOOLS: 设为 1/true/yes 时，流式/非流式 ReAct 不向 API 传 tools[]（回退为仅 system 文本工具说明）。
  JACHIN_HOME_DOTENV_OVERRIDE_PROJECT: 1/true/yes 时，合并 $JACHIN_HOME/.env（或 ~/.jachin/.env）覆盖项目 .env 中同名键；默认 false（仅补全项目未设置的键，如 TAVILY_API_KEY）。
  JACHIN_L3_DEBUG_PRINT_ENV: 启动时打印当前进程全部环境变量（dotenv 合并后）。设为 1/true/yes 为键名排序 + 敏感值脱敏；
    设为 raw/full/all/2 则打印明文（勿外传日志）。未设置或 0/false 则关闭。
"""
from __future__ import annotations

# 最早抑制 httpcore/httpx DEBUG 刷屏（必须在 import httpx 之前）
import logging
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.INFO)
# OpenAI SDK DEBUG 会打印整段 Request options（含全量 tools）；与 jachin.deep 叠加易阻塞管道
for _lg in ("openai", "openai._base_client"):
    logging.getLogger(_lg).setLevel(logging.WARNING)

import argparse
import asyncio
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

# Windows 下强制 UTF-8 + 行缓冲，避免日志/print 块缓冲导致「以为卡死、Ctrl+C 后一次性涌出」
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
    except Exception:
        pass
else:
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(line_buffering=True)
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(line_buffering=True)
    except Exception:
        pass

# 确保项目根在 path 中。PyInstaller 时 __file__ 在 _MEIPASS；便携包根须用 exe 推断（cwd 可能为 System32 等）
if getattr(sys, "frozen", False):
    _exe_parent = Path(sys.executable).resolve().parent
    if _exe_parent.name.lower() == "bin" and _exe_parent.parent.is_dir():
        _root = str(_exe_parent.parent.resolve())
        os.environ.setdefault("JACHIN_APP_ROOT", _root)
    else:
        _root = str(Path.cwd().resolve())
else:
    _root = __file__.rsplit("l3_node", 1)[0].rstrip("/\\")
if _root and _root not in sys.path:
    sys.path.insert(0, _root)

# K11 Playwright 冒烟子进程：frozen 下须用本入口加载 .py，禁止 ``l3_node.exe script.py``（bootloader 不会当 Python 解释器用）
if __name__ == "__main__" and len(sys.argv) > 1:
    if sys.argv[1] == "--jachin-k11-unified-smoke-subprocess":
        from l3_node.k11_subprocess_cli import run_k11_unified_sync

        raise SystemExit(run_k11_unified_sync())
    if sys.argv[1] == "--jachin-k11-p2-compat-subprocess":
        from l3_node.k11_subprocess_cli import run_k11_p2_compat_sync

        raise SystemExit(run_k11_p2_compat_sync())
    if sys.argv[1] == "--jachin-k11-game-open-smoke-subprocess":
        from l3_node.k11_subprocess_cli import run_k11_game_open_smoke_sync

        raise SystemExit(run_k11_game_open_smoke_sync())
    if sys.argv[1] == "--jachin-k11-tongits-autoplay-smoke-subprocess":
        from l3_node.k11_subprocess_cli import run_k11_tongits_autoplay_smoke_sync

        raise SystemExit(run_k11_tongits_autoplay_smoke_sync())

# PMO Copilot 一次性任务：在 logging 初始化前压低控制台刷屏（详情见 pmo_copilot_*.txt / l3_debug.log）
if __name__ == "__main__" and "--run-pmo-copilot" in sys.argv:
    try:
        from l3_node.pmo_copilot_env import apply_pmo_copilot_console_quiet_defaults

        apply_pmo_copilot_console_quiet_defaults()
    except Exception:
        pass

# 桌面/GUI 拉起打包 exe 时无控制台：分配独立控制台（须在 logging 绑定 stdout 之前）
# PMO 一次性子进程禁止弹窗/抢 stdout（见 pmo_copilot_env.JACHIN_L3_CONSOLE=0）
if sys.platform == "win32" and "--run-pmo-copilot" not in sys.argv:
    try:
        from l3_node.win_console import maybe_attach_windows_console

        maybe_attach_windows_console()
        for _stream in (sys.stdout, sys.stderr):
            if _stream is not None and hasattr(_stream, "reconfigure"):
                try:
                    _stream.reconfigure(encoding="utf-8", line_buffering=True)
                except Exception:
                    pass
    except Exception:
        pass

# 最早阶段：启动调试日志（在加载 dotenv 等之前，便于排查 exe 打包问题）
from l3_node.early_log import install_global_exception_hooks, setup_early_logging, trace, get_log_path
_log_path = setup_early_logging()
install_global_exception_hooks()
trace("early_log ready, log_path=%s", _log_path)

# 尽早加载项目根 .env（逻辑在 core.l3_dotenv_merge，供 MCP 解析前再次合并共用）
try:
    trace("loading dotenv...")
    from core.l3_dotenv_merge import merge_l3_dotenv_into_os

    merge_l3_dotenv_into_os(l3_project_root=_root, trace_cb=trace)
except ImportError as e:
    trace("dotenv ImportError: %s", e)
except Exception as e:
    trace("dotenv Exception: %s", e)

try:
    from l3_node.packaged_lark_env import apply_packaged_lark_to_os_environ

    apply_packaged_lark_to_os_environ()
except Exception as e:
    trace("packaged_lark apply: %s", e)

# 立即打印 API Key 状态到调试日志（脱敏：前8+后4字符，便于确认是否加载）
def _mask_key(val: str) -> str:
    if not val or len(val) < 12:
        return "***" if val else "(空)"
    return f"{val[:8]}...{val[-4:]} (len={len(val)})"
_dash = os.environ.get("DASHSCOPE_API_KEY", "")
_openai = os.environ.get("OPENAI_API_KEY", "")
_tavily = os.environ.get("TAVILY_API_KEY", "")
trace("DASHSCOPE_API_KEY=%s", _mask_key(_dash))
trace("OPENAI_API_KEY=%s", _mask_key(_openai))
trace("TAVILY_API_KEY=%s（MCP tavily 占位符依赖）", _mask_key(_tavily))
if not _dash and not _openai:
    trace("WARNING: 未检测到任何 API Key，大模型将不可用")
_sl_admin = (os.environ.get("JACHIN_SAFETY_LOCK_ADMIN_TOKEN") or "").strip()
trace(
    "JACHIN_SAFETY_LOCK_ADMIN_TOKEN=%s（未设置则控制台安全锁审批返回 503；桌面端需在 src-tauri l3_spawn L3_ENV_KEYS 中转发）",
    _mask_key(_sl_admin) if _sl_admin else "(空)",
)

try:
    import importlib.util as _ilu

    _fe_spec = _ilu.find_spec("fastembed")
except Exception:
    _fe_spec = None
trace(
    "Python sys.executable=%s fastembed_find_spec=%s（pip 须装到与 L3 相同的解释器；导入失败时见 Memory Nexus 日志 exc_info）",
    sys.executable,
    bool(_fe_spec),
)


def _is_sensitive_env_key(name: str) -> bool:
    u = (name or "").upper()
    for s in ("KEY", "SECRET", "TOKEN", "PASSWORD", "PRIVATE", "CREDENTIAL", "AUTH"):
        if s in u:
            return True
    if "API" in u and "_" in u:
        return True
    return False


def _format_env_val_for_dump(name: str, val: str, *, raw: bool) -> str:
    if not (val or "").strip():
        return "(空)"
    if raw:
        return val
    if _is_sensitive_env_key(name):
        return _mask_key(val)
    if len(val) > 240:
        return f"{val[:120]}…(len={len(val)})"
    return val


def _env_dump_mode() -> str:
    v = (os.environ.get("JACHIN_L3_DEBUG_PRINT_ENV") or "").strip().lower()
    if not v or v in ("0", "false", "no", "off"):
        return "off"
    if v in ("raw", "full", "all", "2"):
        return "raw"
    if v in ("1", "true", "yes", "on"):
        return "masked"
    return "off"


_log_level = getattr(logging, (os.environ.get("LOG_LEVEL") or "INFO").upper(), logging.INFO)
_je = (os.environ.get("JACHIN_LOG_LEVEL") or "").strip().upper()
_jachin_level = getattr(logging, _je, None) if _je else None
if _jachin_level is None:
    # 根为 WARNING/ERROR 时，Jachin 默认仍打 INFO，避免「关了第三方刷屏却看不见 L3 工具路由」
    _jachin_level = logging.INFO if _log_level >= logging.WARNING else _log_level


class _UTCFormatter(logging.Formatter):
    """使用 UTC 时间的日志格式化器"""
    def formatTime(self, record, datefmt=None):
        from datetime import datetime, timezone
        dt = datetime.fromtimestamp(record.created, tz=timezone.utc)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


# 强制输出到 PowerShell 控制台（stdout），便于复制日志
trace("configuring logging, root=%s jachin=%s", _log_level, _jachin_level)
_log_fmt = _UTCFormatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(_log_fmt)
logging.basicConfig(level=_log_level, handlers=[_handler], force=True)
from l3_node.early_log import (
    attach_file_handler_to_logger,
    configure_l3_runtime_diagnostics,
    is_l3_verbose,
    reattach_file_handler,
)

reattach_file_handler()  # basicConfig 会清除 file handler，需重新挂载

# LOG_LEVEL=WARNING 时根 logger 会丢弃 INFO；为 l3_node / core 单独挂控制台 + 文件，避免 Jachin INFO 被误杀
_jh_shared: logging.StreamHandler | None = None
if _jachin_level < _log_level:
    _jh = logging.StreamHandler(sys.stdout)
    _jh.setFormatter(_log_fmt)
    _jh.setLevel(_jachin_level)
    _jh_shared = _jh
    for _pkg in ("l3_node", "core"):
        _lg = logging.getLogger(_pkg)
        _lg.setLevel(_jachin_level)
        _lg.addHandler(_jh)
        attach_file_handler_to_logger(_lg)
        _lg.propagate = False

configure_l3_runtime_diagnostics()  # JACHIN_L3_DEBUG=1 时 WS/LLM 等 DEBUG 落盘到 l3_debug.log
logger = logging.getLogger("l3_node")
trace("logger ready, debug_log=%s verbose=%s", get_log_path(), is_l3_verbose())


def _maybe_log_startup_environ() -> None:
    """dotenv 合并后打印当前进程全部环境变量（需 JACHIN_L3_DEBUG_PRINT_ENV）。"""
    mode = _env_dump_mode()
    if mode == "off":
        return
    raw = mode == "raw"
    if raw:
        logger.warning(
            "[L3] JACHIN_L3_DEBUG_PRINT_ENV=raw：将打印全部环境变量明文，请勿外传日志或截图"
        )
    keys = sorted(os.environ.keys())
    logger.info(
        "[L3] 环境变量快照（merge_l3_dotenv_into_os 之后）count=%s mode=%s JACHIN_L3_DEBUG_PRINT_ENV=%r",
        len(keys),
        mode,
        os.environ.get("JACHIN_L3_DEBUG_PRINT_ENV"),
    )
    for k in keys:
        try:
            logger.info("[L3 env] %s=%s", k, _format_env_val_for_dump(k, os.environ.get(k, ""), raw=raw))
        except Exception as e:
            logger.info("[L3 env] %s=(format error: %s)", k, e)


_maybe_log_startup_environ()
# 将 l3_node 日志转发到全息监控 SSE，供前端 L3 全息监控面板订阅
try:
    trace("importing log_broadcaster...")
    from l3_node.log_broadcaster import install_broadcast_handler, install_deep_log_handlers

    install_broadcast_handler()
    install_deep_log_handlers(stream_handler=_jh_shared)
    trace("log_broadcaster installed")
except Exception as e:
    trace("log_broadcaster failed: %s", e)
    logger.debug("[L3] 全息监控 handler 安装跳过: %s", e)
# websockets：握手未完成即断开时的 ERROR 堆栈由 early_log.install_websocket_handshake_noise_filters 过滤
logging.getLogger("websockets.server").setLevel(logging.WARNING)
try:
    from l3_node.early_log import install_websocket_handshake_noise_filters

    install_websocket_handshake_noise_filters()
except Exception:
    pass
# 抑制 httpcore/httpx 的 DEBUG 刷屏（connect_tcp/receive_response 等），保留 httpx INFO 请求日志
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.INFO)


class _AsyncioStdioGenShutdownFilter(logging.Filter):
    """Ctrl+C 退出时 MCP stdio_client 异步生成器收尾会触发 asyncio 噪音 ERROR（SDK 已知竞态）。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name != "asyncio":
            return True
        msg = record.getMessage()
        if "closing of asynchronous generator" in msg and "stdio_client" in msg:
            return False
        return True


logging.getLogger("asyncio").addFilter(_AsyncioStdioGenShutdownFilter())
try:
    trace("importing core.log_utc...")
    from core.log_utc import log_utc
    log_utc("[L3] 启动中...", file=sys.stdout)
    trace("core.log_utc done")
except Exception as e:
    trace("core.log_utc failed: %s", e)

# 启动时打印 env 状态，便于排查 Key 分配问题（脱敏显示前8+后4字符）
if _log_path:
    trace("startup status: before debug-log logger.info")
    logger.info("[L3] 调试日志: %s", _log_path)
    trace("startup status: after debug-log logger.info")
    if is_l3_verbose():
        trace("startup status: before verbose logger.info")
        logger.info("[L3] 超详细诊断已开启 (JACHIN_L3_DEBUG/L3_VERBOSE_LOG)，l3_debug.log 将包含 WS/LLM 流式 DEBUG")
        trace("startup status: after verbose logger.info")
    else:
        trace("startup status: before detail-hint logger.info")
        logger.info("[L3] 需要更详细日志时请在 .env 设置 JACHIN_L3_DEBUG=1 后重启 L3")
        trace("startup status: after detail-hint logger.info")
_dash_key = os.environ.get("DASHSCOPE_API_KEY", "")
_openai_key = os.environ.get("OPENAI_API_KEY", "")
if _dash_key:
    trace("startup status: before dashscope logger.info")
    logger.info("[L3] DASHSCOPE_API_KEY 已加载: %s", _mask_key(_dash_key))
    trace("startup status: after dashscope logger.info")
if _openai_key:
    trace("startup status: before openai logger.info")
    logger.info("[L3] OPENAI_API_KEY 已加载: %s", _mask_key(_openai_key))
    trace("startup status: after openai logger.info")
if not _dash_key and not _openai_key:
    trace("startup status: before missing-key logger.warning")
    logger.warning("[L3] 未检测到 DASHSCOPE_API_KEY 或 OPENAI_API_KEY，大模型不可用，将依赖 L2 下发 Key")
    trace("startup status: after missing-key logger.warning")


def _log_l3_code_identity() -> None:
    """启动时打印版本与包路径，可选 git 短哈希，便于确认是否跑在期望的源码上。"""
    try:
        import l3_node as _ln

        ver = getattr(_ln, "__version__", "?")
        pkg = Path(_ln.__file__).resolve().parent
        extra = ""
        try:
            root = Path(_root).resolve() if _root else pkg.parent
            r = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=2,
            )
            if r.returncode == 0 and (r.stdout or "").strip():
                extra = f" git={r.stdout.strip()}"
        except Exception:
            pass
        logger.info("[L3] 代码标识 version=%s package=%s%s", ver, pkg, extra)
    except Exception as e:
        logger.debug("[L3] 代码标识 省略: %s", e)


_log_l3_code_identity()
trace("startup status: after code identity")

trace("startup status: before standalone_engine import")
from l3_node.standalone_engine import create_engine_standalone as _create_engine_standalone
trace("startup status: after standalone_engine import")


async def main() -> None:
    # 旧侧车若误入 main()：必须在单实例锁之前退出，否则会 kill_previous 杀掉桌面常驻 L3
    if "--run-pmo-copilot" in sys.argv:
        from l3_node.pmo_copilot_cli import run_pmo_copilot_main

        raise SystemExit(run_pmo_copilot_main())

    # 抑制客户端断开时的 ConnectionResetError（刷新/关闭页面时常见，非异常）
    _loop = asyncio.get_running_loop()
    _orig = _loop.get_exception_handler()
    if _orig is None:
        _orig = getattr(asyncio, "default_exception_handler", None)
    if _orig is None:
        # Python 3.9 及以下可能无 default_exception_handler，用 logging 兜底
        import logging
        _log = logging.getLogger("asyncio")

        def _fallback(loop, ctx):
            _log.exception("Unhandled exception: %s", ctx.get("message", ctx))

        _orig = _fallback

    def _quiet_handler(loop, ctx):
        exc = ctx.get("exception")
        if exc is not None and isinstance(exc, ConnectionResetError):
            return
        _orig(loop, ctx)

    _loop.set_exception_handler(_quiet_handler)

    from core.single_instance import acquire_single_instance_lock
    # 默认不 taskkill 旧实例（PMO/误双开时避免拖死 start-layer3 常驻 L3）；显式 JACHIN_L3_KILL_PREVIOUS=1 可恢复旧行为
    _kill_prev = (os.environ.get("JACHIN_L3_KILL_PREVIOUS") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    acquire_single_instance_lock("l3", kill_previous=_kill_prev)

    parser = argparse.ArgumentParser(description="L3 节点")
    parser.add_argument("--ws-only", action="store_true", help="仅启动 WebSocket，不连接 L2")
    parser.add_argument("--gateway", action="store_true", help="L2 零信任配对：注册后等待审批")
    parser.add_argument("--port", type=int, default=18981, help="WebSocket 端口 (189xx 系列)")
    parser.add_argument("bi_cmd", nargs="?", default=None, help="输入「BI分析」或「bi分析」执行 BI 每日战报并退出")
    args = parser.parse_args()

    # 终端快捷入口：python -m l3_node BI分析
    if args.bi_cmd and re.search(r"BI分析|bi分析|BI 分析|bi\s*分析", args.bi_cmd.strip(), re.IGNORECASE):
        from l3_node.primitives.skills.bi.bi_daily_report.main_skill import run_bi_daily_report

        logger.info("[L3] 执行 BI 分析...")
        result = run_bi_daily_report()
        if result.get("success"):
            logger.info("[L3] BI 分析完成 output=%d lark=%d", len(result.get("output_paths", [])), result.get("lark_sync_ok", 0))
        else:
            logger.warning("[L3] BI 分析失败: %s", result.get("error"))
        return

    from l3_node.l2_url_util import normalize_l2_base_url

    l2_url = normalize_l2_base_url(os.environ.get("L2_BASE_URL"))
    if os.environ.get("L2_BASE_URL", "").strip() and l2_url != os.environ.get("L2_BASE_URL", "").strip().rstrip("/"):
        logger.info("[L3] L2_BASE_URL 已规范化: %r → %s", os.environ.get("L2_BASE_URL"), l2_url)
    sub_id = os.environ.get("SUB_ACCOUNT_ID", "")

    if args.ws_only:
        # 与 --gateway 一致：先起 HTTP，再热线擎。否则 _create_engine_standalone 抛错时（密钥缺失、
        # JACHIN_ACTIVE_REGION 与 DASHSCOPE_*_CN/_SEA 不匹配等）整进程在 run_http_server 之前就退出，
        # 桌面探测 /api/v3/skills 全失败，误报「L3 技能 API 不可达」。
        from l3_node.bootstrap import run_l3_agent
        from l3_node.http_server import run_http_server, L3_HTTP_PORT
        from l3_node.ws_server import run_ws_server

        trace("main: ws-only mode, HTTP first then engine (same order as gateway)")
        logger.info("L3 WebSocket 独立模式，端口 %d", args.port)
        try:
            await run_http_server(port=L3_HTTP_PORT)
        except Exception:
            logger.exception("[L3] HTTP 服务启动失败")
            raise

        trace("main: ws-only mode, creating engine...")
        try:
            engine = _create_engine_standalone()
        except Exception:
            logger.exception(
                "[L3] 独立模式引擎初始化失败（常见：未配置 DASHSCOPE_API_KEY / OPENAI_API_KEY；"
                "或 JACHIN_ACTIVE_REGION=SEA 但仅配了国内 Key，反之亦然）。"
                "HTTP 技能 API 已启动，可继续用控制台/K11 探测；聊天 WebSocket 须修复 .env 后重启 L3。"
            )
            _park = asyncio.Event()
            await _park.wait()

        trace("main: engine created, loading agent_ref...")
        from l3_node.agent_ref import engine_ref
        from l3_node.background_task_service import start_background_task_runtime

        engine_ref["engine"] = engine
        await start_background_task_runtime(engine)

        # IM 通道（Lark 长连接等）— 按 ~/.jachin/config/im_channels.yaml 启动
        try:
            from l3_node.im_channels import start_im_channels
            from l3_node.im_channels.config import ensure_config_dir

            ensure_config_dir()
            _loop = asyncio.get_running_loop()
            start_im_channels(run_l3_agent, engine, _loop)
            logger.info("[L3] 招聘测试请使用 Lark 长连接发消息（Lark 应用内直接与机器人对话）")
        except Exception as e:
            logger.warning(
                "[L3] IM 通道（Lark 长连接）启动跳过: %s；招聘测试需配置 ~/.jachin/config/im_channels.yaml 的 app_id/app_secret",
                e,
            )
        try:
            await run_ws_server(engine, run_l3_agent, port=args.port)
        finally:
            try:
                from l3_node.graceful_shutdown import run_shutdown_hooks

                await run_shutdown_hooks()
            except Exception as e:
                logger.debug("[L3] run_shutdown_hooks 跳过: %s", e)
        return

    if args.gateway:
        trace("main: gateway mode, importing bootstrap...")
        from l3_node.bootstrap import bootstrap_l3_gateway_pending, heartbeat_loop, run_l3_agent
        from l3_node.ws_server import run_ws_server
        from l3_node.http_server import run_http_server, L3_HTTP_PORT
        trace("main: starting HTTP server...")
        await run_http_server(port=L3_HTTP_PORT)

        from l3_node.agent_ref import engine_ref
        from l3_node.background_task_service import start_background_task_runtime

        sidecars: dict[str, asyncio.Task | None] = {"heartbeat": None, "pull": None}

        engine_prewarm = None
        try:
            engine_prewarm = _create_engine_standalone()
        except Exception as e:
            logger.warning(
                "[L3 Gateway] 无法用 .env 预热线擎，须等待 L2 完成子账号分配后 WebSocket 才可用：%s",
                e,
            )

        if engine_prewarm is not None:
            engine_ref["engine"] = engine_prewarm
            await start_background_task_runtime(engine_prewarm)
            logger.info(
                "[L3 Gateway] 已用 .env 预热线擎：WebSocket 立即开放；L2 处于 pending 时仍可本地聊天。"
                " 请在 L2 控制台为该节点分配子账号以同步技能/密钥（分配后引擎将自动热切换）。"
            )

            async def _gateway_background_prewarm() -> None:
                try:
                    logger.info(
                        "[L3 Gateway] 后台连接 L2: %s（轮询 auth/poll，与聊天并行）",
                        l2_url,
                    )
                    eng2, nid2 = await bootstrap_l3_gateway_pending(
                        l2_base_url=l2_url,
                        on_status=lambda s, m: logger.info("[L3 Gateway] %s: %s", s, m),
                    )
                    engine_ref["engine"] = eng2
                    logger.info("[L3 Gateway] L2 配对完成 node_id=%s，已热切换引擎", nid2)
                    sidecars["heartbeat"] = asyncio.create_task(
                        heartbeat_loop(l2_url, nid2, interval_sec=60.0)
                    )
                    if os.environ.get("JACHIN_MCP_PULL_WORKER", "1").strip().lower() not in ("0", "false", "no"):
                        from l3_node.mcp_delegate_pull_worker import run_mcp_delegate_pull_forever

                        sidecars["pull"] = asyncio.create_task(run_mcp_delegate_pull_forever())
                except RuntimeError as e:
                    msg = str(e)
                    if "无法连接 L2" in msg or "连接 L2 超时" in msg:
                        logger.warning(
                            "[L3] Gateway 后台无法连上 L2（%s），继续使用 .env 预热线擎。",
                            l2_url,
                        )
                    else:
                        logger.exception("[L3 Gateway] 后台配对异常: %s", e)
                except TimeoutError:
                    logger.warning(
                        "[L3 Gateway] L2 审批超时（poll_timeout），继续使用 .env 预热线擎；可重启并完成 L2 分配。"
                    )

            asyncio.create_task(_gateway_background_prewarm(), name="jachin-l3-gateway-pending")
            engine = engine_prewarm
            node_id = "local-fallback"
        else:
            engine = None
            node_id = "local-fallback"
            gateway_l2_ok = False
            try:
                logger.info(
                    "[L3 Gateway] 开始连接 L2: %s（无 .env Key 时须先完成 L2 分配，WebSocket 将延后启动）",
                    l2_url,
                )
                engine, node_id = await bootstrap_l3_gateway_pending(
                    l2_base_url=l2_url,
                    on_status=lambda s, m: logger.info("[L3 Gateway] %s: %s", s, m),
                )
                gateway_l2_ok = True
            except RuntimeError as e:
                msg = str(e)
                if "无法连接 L2" not in msg and "连接 L2 超时" not in msg:
                    raise
                logger.warning(
                    "[L3] Gateway 无法连接 L2（%s），自动降级为独立 WebSocket 模式（等同 --ws-only）。"
                    " 桌面聊天将可直连本机大模型；待 L2 可达后请用 --gateway 重启以完成配对与技能订阅。",
                    l2_url,
                )
                engine = _create_engine_standalone()
                node_id = "local-fallback"
            engine_ref["engine"] = engine
            await start_background_task_runtime(engine)
            if gateway_l2_ok:
                sidecars["heartbeat"] = asyncio.create_task(heartbeat_loop(l2_url, node_id, interval_sec=60.0))
                if os.environ.get("JACHIN_MCP_PULL_WORKER", "1").strip().lower() not in ("0", "false", "no"):
                    from l3_node.mcp_delegate_pull_worker import run_mcp_delegate_pull_forever

                    sidecars["pull"] = asyncio.create_task(run_mcp_delegate_pull_forever())

        logger.info("L3 节点已就绪 node_id=%s，WebSocket 端口 %d", node_id, args.port)
        try:
            from l3_node.im_channels import start_im_channels
            from l3_node.im_channels.config import ensure_config_dir

            ensure_config_dir()
            _im_eng = engine_ref.get("engine") or engine
            start_im_channels(run_l3_agent, _im_eng, asyncio.get_running_loop())
            logger.info("[L3] 招聘测试请使用 Lark 长连接发消息（Lark 应用内直接与机器人对话）")
        except Exception as e:
            logger.warning("[L3] IM 通道（Lark 长连接）启动跳过: %s；招聘测试需配置 ~/.jachin/config/im_channels.yaml", e)
        try:
            await run_ws_server(engine, run_l3_agent, port=args.port)
        finally:
            for _k in ("heartbeat", "pull"):
                _t = sidecars.get(_k)
                if _t:
                    _t.cancel()
                    try:
                        await _t
                    except asyncio.CancelledError:
                        pass
            try:
                from l3_node.graceful_shutdown import run_shutdown_hooks

                await run_shutdown_hooks()
            except Exception as e:
                logger.debug("[L3] run_shutdown_hooks 跳过: %s", e)
        return

    if not sub_id:
        logger.error("请设置 SUB_ACCOUNT_ID 环境变量，或使用 --ws-only / --gateway 模式")
        sys.exit(1)

    # SUB_ACCOUNT_ID 模式已统一为 gateway 流程：注册后需管理员审批分配
    logger.info("SUB_ACCOUNT_ID 模式：向 L2 注册，等待管理员将节点分配给子账号 %s", sub_id)
    from l3_node.bootstrap import bootstrap_l3_gateway_pending, heartbeat_loop, run_l3_agent
    from l3_node.ws_server import run_ws_server
    from l3_node.http_server import run_http_server, L3_HTTP_PORT

    await run_http_server(port=L3_HTTP_PORT)
    engine = None
    node_id = "local-fallback"
    gateway_l2_ok = False
    try:
        logger.info(
            "[L3 Gateway] 开始连接 L2: %s（此阶段可能长时间无新日志，属正常；详见 --gateway 模式说明）",
            l2_url,
        )
        engine, node_id = await bootstrap_l3_gateway_pending(l2_base_url=l2_url)
        gateway_l2_ok = True
    except RuntimeError as e:
        msg = str(e)
        if "无法连接 L2" not in msg and "连接 L2 超时" not in msg:
            raise
        logger.warning(
            "[L3] SUB_ACCOUNT_ID 模式仍无法连接 L2（%s），自动降级为独立 WebSocket 模式。",
            l2_url,
        )
        engine = _create_engine_standalone()
        node_id = "local-fallback"
    from l3_node.agent_ref import engine_ref
    engine_ref["engine"] = engine
    from l3_node.background_task_service import start_background_task_runtime

    await start_background_task_runtime(engine)
    logger.info("L3 节点已就绪 node_id=%s，WebSocket 端口 %d", node_id, args.port)
    # IM 通道（Lark 长连接等）— 招聘测试优先使用长连接
    try:
        from l3_node.im_channels import start_im_channels
        from l3_node.im_channels.config import ensure_config_dir
        ensure_config_dir()
        _loop = asyncio.get_running_loop()
        start_im_channels(run_l3_agent, engine, _loop)
        logger.info("[L3] 招聘测试请使用 Lark 长连接发消息（Lark 应用内直接与机器人对话）")
    except Exception as e:
        logger.warning("[L3] IM 通道（Lark 长连接）启动跳过: %s；招聘测试需配置 ~/.jachin/config/im_channels.yaml", e)
    heartbeat_task = None
    _pull_worker = None
    if gateway_l2_ok:
        heartbeat_task = asyncio.create_task(heartbeat_loop(l2_url, node_id, interval_sec=60.0))
        if os.environ.get("JACHIN_MCP_PULL_WORKER", "1").strip().lower() not in ("0", "false", "no"):
            from l3_node.mcp_delegate_pull_worker import run_mcp_delegate_pull_forever

            _pull_worker = asyncio.create_task(run_mcp_delegate_pull_forever())
    try:
        await run_ws_server(engine, run_l3_agent, port=args.port)
    finally:
        if heartbeat_task:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
        if _pull_worker:
            _pull_worker.cancel()
            try:
                await _pull_worker
            except asyncio.CancelledError:
                pass
        try:
            from l3_node.graceful_shutdown import run_shutdown_hooks

            await run_shutdown_hooks()
        except Exception as e:
            logger.debug("[L3] run_shutdown_hooks 跳过: %s", e)

    logger.info("L3 节点退出")


trace("entrypoint check: __name__=%s argv=%s", __name__, sys.argv)

if __name__ == "__main__":
    trace("entrypoint: entering asyncio runner")
    if "--run-pmo-copilot" in sys.argv:
        try:
            from l3_node.pmo_copilot_cli import run_pmo_copilot_main

            raise SystemExit(run_pmo_copilot_main())
        except KeyboardInterrupt:
            raise SystemExit(130)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("[L3] 已中断 (Ctrl+C)，退出码 130")
        raise SystemExit(130)
    except Exception as e:
        trace("FATAL: %s", e)
        if get_log_path():
            import traceback
            trace("traceback: %s", traceback.format_exc())
        raise
else:
    trace("entrypoint skipped: module __name__ is %s, not __main__", __name__)
