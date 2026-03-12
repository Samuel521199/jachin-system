"""
Plugin Server Base - heavy_process 插件服务端基类
P0-3 混合动力沙箱 - 插件侧约定

heavy_process 插件的入口脚本必须：
1. 接收 --socket /path/to/sock 或读取 JACHIN_SOCKET 环境变量
2. 在指定路径上启动 JSON-RPC 2.0 风格的服务
3. 实现 execute(capability, payload) 方法

本模块提供开箱即用的服务端实现，插件只需继承并实现 execute 逻辑。
"""

import argparse
import json
import logging
import os
import socket
import sys
from typing import Any

logger = logging.getLogger(__name__)


class PluginServerBase:
    """
    heavy_process 插件服务端基类

    用法:
        class MyPlugin(PluginServerBase):
            def execute(self, capability: str, payload: dict) -> dict:
                if capability == "tts":
                    return {"status_code": 200, "payload": self._synthesize(payload)}
                return {"status_code": 404, "error_message": "unknown capability"}

        if __name__ == "__main__":
            PluginServerBase.run(MyPlugin())
    """

    def execute(self, capability: str, payload: dict[str, Any]) -> dict[str, Any]:
        """
        执行插件能力，由子类实现

        Args:
            capability: 能力名称，如 "tts", "render"
            payload: 调用参数

        Returns:
            {"status_code": 200, "payload": ..., "ui_render_schema": ...}
            或 {"status_code": 5xx, "error_message": "..."}
        """
        raise NotImplementedError("子类必须实现 execute 方法")

    def _handle_request(self, line: str) -> str:
        """处理单条 JSON-RPC 请求"""
        req_id = None
        try:
            req = json.loads(line)
            req_id = req.get("id")
            method = req.get("method", "")
            params = req.get("params", {})

            if method != "execute":
                return json.dumps(
                    {"jsonrpc": "2.0", "error": {"code": -32601, "message": f"Unknown method: {method}"}, "id": req_id},
                    ensure_ascii=False,
                ) + "\n"

            capability = params.get("capability", "")
            payload = params.get("payload", {})
            if "payload_b64" in params:
                import base64
                payload["_raw_bytes"] = base64.b64decode(params["payload_b64"])

            result = self.execute(capability, payload)
            return json.dumps({"jsonrpc": "2.0", "result": result, "id": req_id}, ensure_ascii=False) + "\n"
        except Exception as e:
            logger.exception("execute error: %s", e)
            return json.dumps(
                {"jsonrpc": "2.0", "error": {"code": -32603, "message": str(e)}, "id": req_id},
                ensure_ascii=False,
            ) + "\n"

    def run_server(self, socket_path: str) -> None:
        """在 UDS 或 TCP 上启动服务（AF_UNIX 不可用时回退到 TCP localhost）"""
        use_uds = hasattr(socket, "AF_UNIX")
        if use_uds and ":" not in socket_path:
            if os.path.exists(socket_path):
                try:
                    os.unlink(socket_path)
                except OSError:
                    pass
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(socket_path)
        else:
            # TCP 回退：socket_path 形如 127.0.0.1:9123（由 Runner 指定端口）
            if ":" in socket_path:
                host, port_s = socket_path.rsplit(":", 1)
                port = int(port_s)
            else:
                host, port = "127.0.0.1", 0
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((host, port))
        server.listen(5)
        logger.info("插件服务已启动: socket=%s", socket_path)

        try:
            while True:
                conn, _ = server.accept()
                try:
                    buf = b""
                    while True:
                        chunk = conn.recv(4096)
                        if not chunk:
                            break
                        buf += chunk
                        while b"\n" in buf:
                            line, buf = buf.split(b"\n", 1)
                            if line:
                                resp = self._handle_request(line.decode("utf-8"))
                                conn.sendall(resp.encode("utf-8"))
                except (ConnectionResetError, BrokenPipeError):
                    pass
                finally:
                    conn.close()
        except KeyboardInterrupt:
            logger.info("插件服务收到退出信号")
        finally:
            server.close()
            if use_uds and ":" not in socket_path and os.path.exists(socket_path):
                try:
                    os.unlink(socket_path)
                except OSError:
                    pass

    @classmethod
    def run(cls, instance: "PluginServerBase | None" = None) -> None:
        """
        解析 --socket 参数并启动服务

        用法: 在插件入口脚本末尾调用
            if __name__ == "__main__":
                MyPlugin.run()
        """
        parser = argparse.ArgumentParser()
        parser.add_argument("--socket", default=os.environ.get("JACHIN_SOCKET"), help="UDS 路径")
        args = parser.parse_args()
        socket_path = args.socket
        if not socket_path:
            print("Error: 必须指定 --socket 或设置 JACHIN_SOCKET 环境变量", file=sys.stderr)
            sys.exit(1)
        inst = instance or cls()
        inst.run_server(socket_path)
