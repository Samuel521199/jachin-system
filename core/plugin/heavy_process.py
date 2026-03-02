"""
Heavy Process Runner - 重型独立进程沙箱
P0-3 混合动力沙箱 - 分流 B

通过 subprocess 拉起 payload 中的 Python 入口，建立 UDS 通信通道。
Supervisor 守护线程监控子进程，崩溃时指数退避重启；微内核可随时通过 _stop_event 优雅关停。
"""

import json
import logging
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from enum import Enum
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Windows 10+ 支持 AF_UNIX，但路径格式不同
USE_UDS = hasattr(socket, "AF_UNIX")


# 插件生命周期状态（供 Prometheus 等可观测性工具采集）
class PluginState(Enum):
    STARTING = "starting"      # 正在拉起进程
    RUNNING = "running"        # 正常运行中
    CRASHED = "crashed"        # 意外崩溃（等待抢救）
    RESTARTING = "restarting"  # 重启退避中
    STOPPED = "stopped"        # 被内核主动安全关闭
    FATAL = "fatal"            # 抢救无效，彻底死亡


class HeavyProcessClient:
    """
    UDS 客户端：与 heavy_process 插件通信
    协议：JSON-RPC 2.0 风格，每行一条 JSON
    """

    def __init__(self, socket_path: str, timeout: float = 30.0):
        self.socket_path = socket_path
        self.timeout = timeout
        self._sock: Optional[socket.socket] = None

    def connect(self) -> bool:
        """建立连接"""
        try:
            if USE_UDS:
                self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self._sock.settimeout(self.timeout)
                self._sock.connect(self.socket_path)
            else:
                # Windows 回退：使用 localhost TCP（需插件支持）
                host, port = self._parse_tcp_fallback()
                self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self._sock.settimeout(self.timeout)
                self._sock.connect((host or "127.0.0.1", port or 0))
            return True
        except Exception as e:
            logger.error("HeavyProcessClient connect failed: %s", e)
            return False

    def _parse_tcp_fallback(self) -> tuple[Optional[str], Optional[int]]:
        """UDS 不可用时解析 TCP 回退地址，如 127.0.0.1:9123"""
        if ":" in self.socket_path and not self.socket_path.startswith("/"):
            parts = self.socket_path.rsplit(":", 1)
            try:
                return parts[0], int(parts[1])
            except ValueError:
                pass
        return None, None

    def execute(
        self, capability: str, payload: dict[str, Any] | bytes | None = None
    ) -> dict[str, Any]:
        """
        调用插件的 execute 方法

        Returns:
            {"status_code": 200, "payload": ..., "ui_render_schema": ...}
            或 {"status_code": 500, "error_message": "..."}
        """
        if not self._sock:
            if not self.connect():
                return {"status_code": 503, "error_message": "无法连接插件进程"}
        try:
            req = {
                "jsonrpc": "2.0",
                "method": "execute",
                "params": {
                    "capability": capability,
                    "payload": payload if isinstance(payload, dict) else {"raw": "<bytes>"},
                },
                "id": int(time.time() * 1000),
            }
            if isinstance(payload, bytes):
                req["params"]["payload_b64"] = __import__("base64").b64encode(payload).decode()
            msg = json.dumps(req, ensure_ascii=False) + "\n"
            self._sock.sendall(msg.encode("utf-8"))

            # 读取响应（简单行协议）
            buf = b""
            while b"\n" not in buf:
                chunk = self._sock.recv(4096)
                if not chunk:
                    raise ConnectionError("插件进程断开连接")
                buf += chunk
            line = buf.split(b"\n", 1)[0].decode("utf-8")
            resp = json.loads(line)
            if "error" in resp:
                return {
                    "status_code": 500,
                    "error_message": resp["error"].get("message", str(resp["error"])),
                }
            result = resp.get("result", {})
            return result if isinstance(result, dict) else {"status_code": 200, "payload": result}
        except (ConnectionError, BrokenPipeError, OSError) as e:
            logger.warning("插件通信断开: %s", e)
            self._sock = None
            return {"status_code": 503, "error_message": f"插件进程断开: {e}"}
        except Exception as e:
            logger.error("execute failed: %s", e, exc_info=True)
            return {"status_code": 500, "error_message": str(e)}

    def close(self) -> None:
        """关闭连接"""
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None


class HeavyProcessRunner:
    """
    重型进程沙箱：拉起 Python 子进程，建立 UDS 通道
    Supervisor 守护线程监控子进程，崩溃时指数退避重启
    """

    def __init__(
        self,
        plugin_id: str,
        payload_dir: str,
        manifest: dict[str, Any],
        socket_base_dir: Optional[str] = None,
        max_restarts: int = 5,
    ):
        self.plugin_id = plugin_id
        self.payload_dir = Path(payload_dir)
        self.manifest = manifest
        self.socket_base = Path(socket_base_dir or tempfile.gettempdir())
        self.max_restarts = max_restarts

        self._process: Optional[subprocess.Popen] = None
        self._client: Optional[HeavyProcessClient] = None
        self._socket_path: Optional[str] = None

        # Supervisor 状态机
        self.state = PluginState.STOPPED
        self.restart_count = 0
        self._stop_event = threading.Event()
        self._monitor_thread: Optional[threading.Thread] = None

    def _resolve_entrypoint(self) -> Path:
        """解析入口文件路径（支持 payload/ 与根目录两种结构）"""
        entry = self.manifest.get("entrypoint") or self.manifest.get("entry", "main.py")
        # JMP 2.0: payload/main.py
        payload_entry = self.payload_dir / entry
        if payload_entry.exists():
            return payload_entry
        # 旧结构: main.py 在根目录
        root_entry = self.payload_dir.parent / entry if self.payload_dir.name == "payload" else self.payload_dir / entry
        if root_entry.exists():
            return root_entry
        raise FileNotFoundError(f"入口文件不存在: {entry} (payload={self.payload_dir})")

    def _get_socket_path(self) -> str:
        """生成 UDS 路径或 TCP 地址（AF_UNIX 不可用时用 TCP）"""
        safe_id = self.plugin_id.replace(".", "_").replace(":", "_")[:32]
        if USE_UDS:
            self.socket_base.mkdir(parents=True, exist_ok=True)
            return str(self.socket_base / f"jachin_uds_{safe_id}.sock")
        # TCP 回退：按 plugin_id 哈希分配端口，避免冲突
        port = 9100 + (abs(hash(safe_id)) % 800)
        return f"127.0.0.1:{port}"

    def start(self) -> HeavyProcessClient:
        """
        主内核调用此方法启动插件，返回通信客户端

        Returns:
            HeavyProcessClient 实例，用于 execute() 调用

        Raises:
            FileNotFoundError: 入口文件不存在
            RuntimeError: 进程启动失败
        """
        self._stop_event.clear()
        self.state = PluginState.STARTING
        self.restart_count = 0

        self._launch_process()

        # 等待插件就绪（监听 socket）
        client = HeavyProcessClient(self._socket_path, timeout=30.0)
        for _ in range(50):  # 最多 5 秒
            time.sleep(0.1)
            if client.connect():
                self._client = client
                self.state = PluginState.RUNNING
                break
        else:
            # 超时，检查进程是否已退出
            if self._process.poll() is not None:
                stderr = ""
                if self._process.stderr:
                    stderr = self._process.stderr.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"插件进程异常退出: {stderr}")
            raise RuntimeError("插件进程启动超时，未能在 socket 上建立连接")

        # 启动 Supervisor 守护线程，死死盯着子进程
        self._monitor_thread = threading.Thread(
            target=self._supervisor_loop,
            name=f"Supervisor-{self.plugin_id}",
            daemon=True,  # 主程序退出时自动销毁
        )
        self._monitor_thread.start()

        logger.info("🚀 武器 [%s] 引擎已点火，Supervisor 挂载完毕。", self.plugin_id)
        return client

    def _launch_process(self) -> None:
        """底层物理启动逻辑"""
        entry_path = self._resolve_entrypoint()
        self._socket_path = self._get_socket_path()

        env = os.environ.copy()
        env["JACHIN_SOCKET"] = self._socket_path
        env["JACHIN_PLUGIN_ID"] = self.plugin_id
        _project_root = Path(__file__).resolve().parents[2]
        env["PYTHONPATH"] = os.pathsep.join(
            [str(_project_root)] + env.get("PYTHONPATH", "").split(os.pathsep)
        )

        cwd = str(entry_path.parent)
        cmd = [sys.executable, str(entry_path), "--socket", self._socket_path]

        logger.info(
            "启动重型插件进程: plugin_id=%s, entry=%s, socket=%s",
            self.plugin_id,
            entry_path.name,
            self._socket_path,
        )
        try:
            self._process = subprocess.Popen(
                cmd,
                cwd=cwd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except Exception as e:
            raise RuntimeError(f"插件进程启动失败: {e}") from e

    def _supervisor_loop(self) -> None:
        """
        Supervisor 核心心跳循环：阻塞等待子进程状态
        subprocess.wait() 系统级阻塞，插件不死则线程休眠，几乎不吃 CPU
        """
        while not self._stop_event.is_set():
            if self._process is None:
                break

            # wait() 阻塞直到子进程退出；不退出则安静待着
            ret_code = self._process.wait()

            if self._stop_event.is_set():
                break

            # 🚨 进程非预期死亡，触发急救
            self.state = PluginState.CRASHED
            logger.error("🚨 警报: 武器 [%s] 意外崩溃! 退出码: %s", self.plugin_id, ret_code)

            self._handle_crash()

    def _handle_crash(self) -> None:
        """指数退避（Exponential Backoff）抢救协议"""
        if self.restart_count >= self.max_restarts:
            self.state = PluginState.FATAL
            logger.critical(
                "💀 武器 [%s] 连续崩溃超过 %s 次，已放弃抢救，进入 FATAL 状态。",
                self.plugin_id,
                self.max_restarts,
            )
            return

        self.state = PluginState.RESTARTING
        self.restart_count += 1

        backoff_time = 2**self.restart_count
        logger.warning(
            "⏳ 正在启动备用电源... %s 秒后执行第 %s 次重启。",
            backoff_time,
            self.restart_count,
        )

        # 使用 wait 而非 sleep：微内核要求关机时可瞬间中断
        if self._stop_event.wait(timeout=backoff_time):
            return

        logger.info("🔄 正在重启武器 [%s]...", self.plugin_id)
        self._launch_process()
        self.state = PluginState.RUNNING

    def stop(self) -> None:
        """微内核主动安全卸载插件（优雅关机）"""
        logger.info("🛑 正在安全卸载武器 [%s]...", self.plugin_id)
        self._stop_event.set()
        self.state = PluginState.STOPPED

        if self._client:
            self._client.close()
            self._client = None

        if self._process and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                logger.warning(
                    "⚠️ 武器 [%s] 拒绝安全关闭，执行强制抹杀 (SIGKILL)。",
                    self.plugin_id,
                )
                self._process.kill()
        self._process = None

    @property
    def is_alive(self) -> bool:
        """进程是否存活"""
        return self._process is not None and self._process.poll() is None
