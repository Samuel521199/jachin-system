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
"""
from __future__ import annotations

# 最早抑制 httpcore/httpx DEBUG 刷屏（必须在 import httpx 之前）
import logging
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.INFO)

import argparse
import asyncio
import logging
import os
import re
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

# 确保项目根在 path 中。PyInstaller 时 __file__ 为 _MEIPASS/__main__.py 不含 l3_node，需用 cwd
if getattr(sys, "frozen", False):
    _root = str(Path.cwd())
else:
    _root = __file__.rsplit("l3_node", 1)[0].rstrip("/\\")
if _root and _root not in sys.path:
    sys.path.insert(0, _root)

# 桌面/GUI 拉起打包 exe 时无控制台：分配独立控制台（须在 logging 绑定 stdout 之前）
if sys.platform == "win32":
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
from l3_node.early_log import setup_early_logging, trace, get_log_path
_log_path = setup_early_logging()
trace("early_log ready, log_path=%s", _log_path)

# 尽早加载项目根 .env，确保 DASHSCOPE_API_KEY 等被 L3 继承（桌面端 spawn 时可能未继承）
# PyInstaller 时 __file__ 为 _MEIPASS/__main__.py 不含 l3_node，_root 推导会错，故 frozen 时仅用 cwd
try:
    trace("loading dotenv...")
    from dotenv import load_dotenv
    _env_loaded = False
    if getattr(sys, "frozen", False):
        _env_candidates = [Path.cwd() / ".env", Path.home() / ".jachin" / ".env"]
    else:
        _pr = Path(_root) / ".env"
        _pc = Path.cwd() / ".env"
        _env_candidates = [_pr, _pc] if _pr != _pc else [_pr]
        # 排除 PyInstaller 错误推导（__file__ 为 _MEIPASS/__main__.py 时 _root 会错）
        _env_candidates = [p for p in _env_candidates if "_MEIPASS" not in str(p) and "__main__.py" not in str(p)]
        if not _env_candidates:
            _env_candidates = [Path.cwd() / ".env"]
    for _p in _env_candidates:
        trace(".env path=%s exists=%s", _p, _p.exists())
        if _p.exists():
            load_dotenv(_p, encoding="utf-8")
            _env_loaded = True
            trace(".env loaded from %s", _p)
            break
    if not _env_loaded:
        _cwd = Path.cwd()
        for _ in range(8):
            _p = _cwd / ".env"
            if _p.exists():
                load_dotenv(_p, encoding="utf-8")
                _env_loaded = True
                trace(".env loaded from %s (cwd search)", _p)
                break
            _cwd = _cwd.parent if _cwd.parent != _cwd else _cwd
            if not _cwd or str(_cwd) == "/":
                break
    # 兜底：~/.jachin/.env（桌面端打包/便携运行时，项目 .env 可能不可用）
    if not _env_loaded:
        _home_env = Path.home() / ".jachin" / ".env"
        if _home_env.exists():
            load_dotenv(_home_env, encoding="utf-8")
            _env_loaded = True
            trace(".env loaded from %s (home fallback)", _home_env)
    trace("dotenv done, env_loaded=%s", _env_loaded)
except ImportError as e:
    trace("dotenv ImportError: %s", e)
except Exception as e:
    trace("dotenv Exception: %s", e)

# 立即打印 API Key 状态到调试日志（脱敏：前8+后4字符，便于确认是否加载）
def _mask_key(val: str) -> str:
    if not val or len(val) < 12:
        return "***" if val else "(空)"
    return f"{val[:8]}...{val[-4:]} (len={len(val)})"
_dash = os.environ.get("DASHSCOPE_API_KEY", "")
_openai = os.environ.get("OPENAI_API_KEY", "")
trace("DASHSCOPE_API_KEY=%s", _mask_key(_dash))
trace("OPENAI_API_KEY=%s", _mask_key(_openai))
if not _dash and not _openai:
    trace("WARNING: 未检测到任何 API Key，大模型将不可用")

_log_level = getattr(logging, (os.environ.get("LOG_LEVEL") or "INFO").upper(), logging.INFO)


class _UTCFormatter(logging.Formatter):
    """使用 UTC 时间的日志格式化器"""
    def formatTime(self, record, datefmt=None):
        from datetime import datetime, timezone
        dt = datetime.fromtimestamp(record.created, tz=timezone.utc)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


# 强制输出到 PowerShell 控制台（stdout），便于复制日志
trace("configuring logging, level=%s", _log_level)
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(_UTCFormatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
logging.basicConfig(level=_log_level, handlers=[_handler], force=True)
from l3_node.early_log import reattach_file_handler
reattach_file_handler()  # basicConfig 会清除 file handler，需重新挂载
logger = logging.getLogger("l3_node")
trace("logger ready, debug_log=%s", get_log_path())
# 将 l3_node 日志转发到全息监控 SSE，供前端 L3 全息监控面板订阅
try:
    trace("importing log_broadcaster...")
    from l3_node.log_broadcaster import install_broadcast_handler
    install_broadcast_handler()
    trace("log_broadcaster installed")
except Exception as e:
    trace("log_broadcaster failed: %s", e)
    logger.debug("[L3] 全息监控 handler 安装跳过: %s", e)
# 抑制 websockets 客户端断开时的 ConnectionClosedError 堆栈（刷新/关闭页面时常见）
logging.getLogger("websockets.server").setLevel(logging.WARNING)
# 抑制 httpcore/httpx 的 DEBUG 刷屏（connect_tcp/receive_response 等），保留 httpx INFO 请求日志
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.INFO)
try:
    trace("importing core.log_utc...")
    from core.log_utc import log_utc
    log_utc("[L3] 启动中...", file=sys.stdout)
    trace("core.log_utc done")
except Exception as e:
    trace("core.log_utc failed: %s", e)

# 启动时打印 env 状态，便于排查 Key 分配问题（脱敏显示前8+后4字符）
if _log_path:
    logger.info("[L3] 调试日志: %s", _log_path)
_dash_key = os.environ.get("DASHSCOPE_API_KEY", "")
_openai_key = os.environ.get("OPENAI_API_KEY", "")
if _dash_key:
    logger.info("[L3] DASHSCOPE_API_KEY 已加载: %s", _mask_key(_dash_key))
if _openai_key:
    logger.info("[L3] OPENAI_API_KEY 已加载: %s", _mask_key(_openai_key))
if not _dash_key and not _openai_key:
    logger.warning("[L3] 未检测到 DASHSCOPE_API_KEY 或 OPENAI_API_KEY，大模型不可用，将依赖 L2 下发 Key")


def _create_engine_standalone():
    """仅用环境变量创建引擎，不连接 L2。有 DASHSCOPE 时默认 qwen3.5-plus，降级用 flash，避免连未启动的 Ollama。"""
    try:
        trace("_create_engine_standalone: importing LiteLLMEngine...")
        from l3_node.llm_client import LiteLLMEngine, SecurityContext

        ctx = SecurityContext()
        if os.environ.get("OPENAI_API_KEY"):
            ctx.set_key("openai", os.environ["OPENAI_API_KEY"])
        if os.environ.get("DASHSCOPE_API_KEY"):
            ctx.set_key("dashscope", os.environ["DASHSCOPE_API_KEY"])
        fallback = None
        default_model = "gpt-4o-mini"
        if ctx.get_key("dashscope"):
            try:
                from core.llm_provider import DASHSCOPE_ECON_FALLBACK_MODEL

                fallback = [DASHSCOPE_ECON_FALLBACK_MODEL]
            except ImportError:
                fallback = ["dashscope/qwen3.5-flash-2026-02-23"]
            default_model = os.environ.get("LLM_MODEL", "qwen3.5-plus")
        _timeout = float(os.environ.get("LLM_TIMEOUT", "180"))
        engine = LiteLLMEngine(
            security_context=ctx,
            model_name=os.environ.get("L3_MODEL", default_model),
            fallback_models=fallback,
            timeout=_timeout,
            max_attempts=2,
        )
        trace("_create_engine_standalone: importing register_host_services...")
        from core.wasm_runner import register_host_services
        from l3_node.l2_url_util import normalize_l2_base_url

        register_host_services(
            llm_engine=engine, l2_base_url=normalize_l2_base_url(os.environ.get("L2_BASE_URL"))
        )
        trace("_create_engine_standalone: done")
        return engine
    except Exception as e:
        trace("_create_engine_standalone FAILED: %s", e)
        raise


async def main() -> None:
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
    acquire_single_instance_lock("l3", kill_previous=True)  # 同设备仅允许一个 L3，启动时杀死旧实例

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
        trace("main: ws-only mode, creating engine...")
        engine = _create_engine_standalone()
        trace("main: engine created, loading agent_ref...")
        from l3_node.agent_ref import engine_ref
        engine_ref["engine"] = engine
        from l3_node.background_task_service import start_background_task_runtime

        await start_background_task_runtime(engine)
        from l3_node.bootstrap import run_l3_agent
        from l3_node.ws_server import run_ws_server
        from l3_node.http_server import run_http_server, L3_HTTP_PORT

        logger.info("L3 WebSocket 独立模式，端口 %d", args.port)
        # IM 通道（Lark 长连接等）— 按 ~/.jachin/config/im_channels.yaml 启动
        try:
            from l3_node.im_channels import start_im_channels
            from l3_node.im_channels.config import ensure_config_dir
            ensure_config_dir()
            _loop = asyncio.get_running_loop()
            start_im_channels(run_l3_agent, engine, _loop)
            logger.info("[L3] 招聘测试请使用 Lark 长连接发消息（Lark 应用内直接与机器人对话）")
        except Exception as e:
            logger.warning("[L3] IM 通道（Lark 长连接）启动跳过: %s；招聘测试需配置 ~/.jachin/config/im_channels.yaml 的 app_id/app_secret", e)
        try:
            await run_http_server(port=L3_HTTP_PORT)
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
        engine, node_id = await bootstrap_l3_gateway_pending(
            l2_base_url=l2_url,
            on_status=lambda s, m: logger.info("[L3 Gateway] %s: %s", s, m),
        )
        from l3_node.agent_ref import engine_ref
        engine_ref["engine"] = engine
        from l3_node.background_task_service import start_background_task_runtime

        await start_background_task_runtime(engine)
        logger.info("L3 节点已就绪 node_id=%s，WebSocket 端口 %d", node_id, args.port)
        # IM 通道（Lark 长连接等）
        try:
            from l3_node.im_channels import start_im_channels
            from l3_node.im_channels.config import ensure_config_dir
            ensure_config_dir()
            _loop = asyncio.get_running_loop()
            start_im_channels(run_l3_agent, engine, _loop)
            logger.info("[L3] 招聘测试请使用 Lark 长连接发消息（Lark 应用内直接与机器人对话）")
        except Exception as e:
            logger.warning("[L3] IM 通道（Lark 长连接）启动跳过: %s；招聘测试需配置 ~/.jachin/config/im_channels.yaml", e)
        heartbeat_task = asyncio.create_task(heartbeat_loop(l2_url, node_id, interval_sec=60.0))
        _pull_worker = None
        if os.environ.get("JACHIN_MCP_PULL_WORKER", "1").strip().lower() not in ("0", "false", "no"):
            from l3_node.mcp_delegate_pull_worker import run_mcp_delegate_pull_forever

            _pull_worker = asyncio.create_task(run_mcp_delegate_pull_forever())
        try:
            await run_ws_server(engine, run_l3_agent, port=args.port)
        finally:
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
    engine, node_id = await bootstrap_l3_gateway_pending(l2_base_url=l2_url)
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
    heartbeat_task = asyncio.create_task(heartbeat_loop(l2_url, node_id, interval_sec=60.0))
    _pull_worker = None
    if os.environ.get("JACHIN_MCP_PULL_WORKER", "1").strip().lower() not in ("0", "false", "no"):
        from l3_node.mcp_delegate_pull_worker import run_mcp_delegate_pull_forever

        _pull_worker = asyncio.create_task(run_mcp_delegate_pull_forever())
    try:
        await run_ws_server(engine, run_l3_agent, port=args.port)
    finally:
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


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        trace("FATAL: %s", e)
        if get_log_path():
            import traceback
            trace("traceback: %s", traceback.format_exc())
        raise
