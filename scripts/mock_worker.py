#!/usr/bin/env python3
"""
Jachin Nexus v8.0 — 工蜂 (Worker Drone) 极简测试脚本

模拟局域网空闲 Mac Mini / 树莓派：连接 ws://localhost:8080/sensory，
声明 worker_video_encode 能力，接单后模拟 10 秒转码并回传结果。

用法: python scripts/mock_worker.py
"""
import asyncio
import json
import sys
from pathlib import Path

import websockets

WS_URL = "ws://localhost:8080/sensory"
WORKER_ID = "Node-MockDrone"


async def main() -> None:
    print(f"[🐝] 工蜂启动，连接 {WS_URL}...")
    try:
        async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=10) as ws:
            # 1. 声明能力
            await ws.send(json.dumps({"type": "manifest", "caps": ["worker_video_encode"]}))
            print(f"[🐝] 已声明能力: worker_video_encode")

            while True:
                msg = await ws.recv()
                data = json.loads(msg)
                step_type = data.get("step_type", "")

                if step_type == "task_offer":
                    task_id = data.get("task_id", "")
                    tool = data.get("tool", "")
                    print(f"[🐝] 收到悬赏: {task_id} ({tool})，竞标接单...")
                    await ws.send(json.dumps({
                        "action": "TASK_CLAIM",
                        "task_id": task_id,
                        "worker_id": WORKER_ID,
                    }))

                elif step_type == "task_assigned":
                    task_id = data.get("task_id", "")
                    payload = data.get("payload", {})
                    print(f"[🐝] 接单成功！任务 {task_id}，参数: {payload}")
                    print("[🐝] 模拟转码中... (10s)")
                    await asyncio.sleep(10)
                    await ws.send(json.dumps({
                        "action": "TASK_RESULT",
                        "task_id": task_id,
                        "data": "转码成功，输出已写入 ~/output.mp4",
                    }))
                    print("[🐝] 结果已回传，主脑将苏醒。")

    except websockets.exceptions.ConnectionClosed:
        print("[🐝] 连接已断开")
    except ConnectionRefusedError:
        print("[🐝] 无法连接，请先启动 daemon: python -m core.daemon")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n[🐝] 工蜂休眠")


if __name__ == "__main__":
    asyncio.run(main())
