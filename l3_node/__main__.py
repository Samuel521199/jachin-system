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
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

# 确保项目根在 path 中
_root = __file__.rsplit("l3_node", 1)[0].rstrip("/\\")
if _root and _root not in sys.path:
    sys.path.insert(0, _root)

# 尽早加载项目根 .env，确保 DASHSCOPE_API_KEY 等被 L3 继承（桌面端 spawn 时可能未继承）
# 路径优先级：1) __file__ 推导的项目根 2) cwd 3) 从 cwd 向上查找（PyInstaller/Sidecar 兼容）
try:
    from dotenv import load_dotenv
    _env_loaded = False
    for _p in [Path(_root) / ".env", Path.cwd() / ".env"]:
        if _p.exists():
            load_dotenv(_p, encoding="utf-8")
            _env_loaded = True
            break
    if not _env_loaded:
        _cwd = Path.cwd()
        for _ in range(8):
            _p = _cwd / ".env"
            if _p.exists():
                load_dotenv(_p, encoding="utf-8")
                break
            _cwd = _cwd.parent if _cwd.parent != _cwd else _cwd
            if not _cwd or str(_cwd) == "/":
                break
except ImportError:
    pass

_log_level = getattr(logging, (os.environ.get("LOG_LEVEL") or "INFO").upper(), logging.INFO)
# 强制输出到 PowerShell 控制台（stdout），便于复制日志
logging.basicConfig(
    level=_log_level,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
    force=True,
)
logger = logging.getLogger("l3_node")
# 将 l3_node 日志转发到全息监控 SSE，供前端 L3 全息监控面板订阅
try:
    from l3_node.log_broadcaster import install_broadcast_handler
    install_broadcast_handler()
except Exception as e:
    logger.debug("[L3] 全息监控 handler 安装跳过: %s", e)
print("[L3] 启动中...", file=sys.stdout, flush=True)

# 启动时打印 env 状态，便于排查 Key 分配问题
if os.environ.get("DASHSCOPE_API_KEY"):
    logger.info("[L3] 环境变量 DASHSCOPE_API_KEY 已加载")
elif os.environ.get("OPENAI_API_KEY"):
    logger.info("[L3] 环境变量 OPENAI_API_KEY 已加载")
else:
    logger.warning("[L3] 未检测到 DASHSCOPE_API_KEY 或 OPENAI_API_KEY，将依赖 L2 下发 Key")


def _create_engine_standalone():
    """仅用环境变量创建引擎，不连接 L2。有 DASHSCOPE 时用 qwen3.5-flash-2026-02-23，避免连未启动的 Ollama。"""
    from l3_node.llm_client import LiteLLMEngine, SecurityContext

    ctx = SecurityContext()
    if os.environ.get("OPENAI_API_KEY"):
        ctx.set_key("openai", os.environ["OPENAI_API_KEY"])
    if os.environ.get("DASHSCOPE_API_KEY"):
        ctx.set_key("dashscope", os.environ["DASHSCOPE_API_KEY"])
    fallback = None
    default_model = "gpt-4o-mini"
    if ctx.get_key("dashscope"):
        fallback = ["dashscope/qwen3.5-flash-2026-02-23"]
        default_model = os.environ.get("LLM_MODEL", "qwen3.5-flash-2026-02-23")
    _timeout = float(os.environ.get("LLM_TIMEOUT", "180"))
    engine = LiteLLMEngine(
        security_context=ctx,
        model_name=os.environ.get("L3_MODEL", default_model),
        fallback_models=fallback,
        timeout=_timeout,
        max_attempts=2,
    )
    from core.wasm_runner import register_host_services
    register_host_services(llm_engine=engine, l2_base_url=os.environ.get("L2_BASE_URL", "http://localhost:18888"))
    return engine


async def main() -> None:
    parser = argparse.ArgumentParser(description="L3 节点")
    parser.add_argument("--ws-only", action="store_true", help="仅启动 WebSocket，不连接 L2")
    parser.add_argument("--gateway", action="store_true", help="L2 零信任配对：注册后等待审批")
    parser.add_argument("--port", type=int, default=18981, help="WebSocket 端口 (189xx 系列)")
    args = parser.parse_args()

    l2_url = os.environ.get("L2_BASE_URL", "http://localhost:18888")
    sub_id = os.environ.get("SUB_ACCOUNT_ID", "")

    if args.ws_only:
        engine = _create_engine_standalone()
        from l3_node.agent_ref import engine_ref
        engine_ref["engine"] = engine
        from l3_node.bootstrap import run_l3_agent
        from l3_node.ws_server import run_ws_server
        from l3_node.http_server import run_http_server, L3_HTTP_PORT

        logger.info("L3 WebSocket 独立模式，端口 %d", args.port)
        await run_http_server(port=L3_HTTP_PORT)
        await run_ws_server(engine, run_l3_agent, port=args.port)
        return

    if args.gateway:
        from l3_node.bootstrap import bootstrap_l3_gateway_pending, heartbeat_loop, run_l3_agent
        from l3_node.ws_server import run_ws_server
        from l3_node.http_server import run_http_server, L3_HTTP_PORT

        await run_http_server(port=L3_HTTP_PORT)
        engine, node_id = await bootstrap_l3_gateway_pending(
            l2_base_url=l2_url,
            on_status=lambda s, m: logger.info("[L3 Gateway] %s: %s", s, m),
        )
        from l3_node.agent_ref import engine_ref
        engine_ref["engine"] = engine
        logger.info("L3 节点已就绪 node_id=%s，WebSocket 端口 %d", node_id, args.port)
        heartbeat_task = asyncio.create_task(heartbeat_loop(l2_url, node_id, interval_sec=60.0))
        try:
            await run_ws_server(engine, run_l3_agent, port=args.port)
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
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
    logger.info("L3 节点已就绪 node_id=%s，WebSocket 端口 %d", node_id, args.port)
    heartbeat_task = asyncio.create_task(heartbeat_loop(l2_url, node_id, interval_sec=60.0))
    try:
        await run_ws_server(engine, run_l3_agent, port=args.port)
    finally:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass

    logger.info("L3 节点退出")


if __name__ == "__main__":
    asyncio.run(main())
