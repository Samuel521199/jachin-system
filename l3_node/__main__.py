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

# 确保项目根在 path 中
_root = __file__.rsplit("l3_node", 1)[0].rstrip("/\\")
if _root and _root not in sys.path:
    sys.path.insert(0, _root)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("l3_node")


def _create_engine_standalone():
    """仅用环境变量创建引擎，不连接 L2。"""
    from l3_node.llm_client import LiteLLMEngine, SecurityContext

    ctx = SecurityContext()
    if os.environ.get("OPENAI_API_KEY"):
        ctx.set_key("openai", os.environ["OPENAI_API_KEY"])
    if os.environ.get("DASHSCOPE_API_KEY"):
        ctx.set_key("dashscope", os.environ["DASHSCOPE_API_KEY"])
    return LiteLLMEngine(
        security_context=ctx,
        model_name=os.environ.get("L3_MODEL", "gpt-4o-mini"),
        fallback_models=["ollama/qwen2.5"],
        timeout=60.0,
        max_attempts=2,
    )


async def main() -> None:
    parser = argparse.ArgumentParser(description="L3 节点")
    parser.add_argument("--ws-only", action="store_true", help="仅启动 WebSocket，不连接 L2")
    parser.add_argument("--gateway", action="store_true", help="L2 零信任配对：注册后等待审批")
    parser.add_argument("--port", type=int, default=18881, help="WebSocket 端口")
    args = parser.parse_args()

    l2_url = os.environ.get("L2_BASE_URL", "http://localhost:18888")
    sub_id = os.environ.get("SUB_ACCOUNT_ID", "")

    if args.ws_only:
        engine = _create_engine_standalone()
        from l3_node.bootstrap import run_l3_agent
        from l3_node.ws_server import run_ws_server

        logger.info("L3 WebSocket 独立模式，端口 %d", args.port)
        await run_ws_server(engine, run_l3_agent, port=args.port)
        return

    if args.gateway:
        from l3_node.bootstrap import bootstrap_l3_gateway_pending, run_l3_agent
        from l3_node.ws_server import run_ws_server

        engine, node_id = await bootstrap_l3_gateway_pending(
            l2_base_url=l2_url,
            on_status=lambda s, m: logger.info("[L3 Gateway] %s: %s", s, m),
        )
        logger.info("L3 节点已就绪 node_id=%s，WebSocket 端口 %d", node_id, args.port)
        await run_ws_server(engine, run_l3_agent, port=args.port)
        return

    if not sub_id:
        logger.error("请设置 SUB_ACCOUNT_ID 环境变量，或使用 --ws-only / --gateway 模式")
        sys.exit(1)

    # SUB_ACCOUNT_ID 模式已统一为 gateway 流程：注册后需管理员审批分配
    logger.info("SUB_ACCOUNT_ID 模式：向 L2 注册，等待管理员将节点分配给子账号 %s", sub_id)
    from l3_node.bootstrap import bootstrap_l3_gateway_pending, run_l3_agent
    from l3_node.ws_server import run_ws_server

    engine, node_id = await bootstrap_l3_gateway_pending(l2_base_url=l2_url)
    logger.info("L3 节点已就绪 node_id=%s，WebSocket 端口 %d", node_id, args.port)

    await run_ws_server(engine, run_l3_agent, port=args.port)

    logger.info("L3 节点退出")


if __name__ == "__main__":
    asyncio.run(main())
