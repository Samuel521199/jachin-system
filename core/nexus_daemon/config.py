"""
Nexus Daemon 配置加载

优先级：环境变量 > ~/.jachin/nexus_config.json > 默认值
配对成功后，instance_id 与 access_token 应持久化到配置文件。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


def _config_path() -> Path:
    """配置文件路径：~/.jachin/nexus_config.json"""
    home = Path.home()
    return home / ".jachin" / "nexus_config.json"


@dataclass
class DaemonConfig:
    """Daemon 运行配置"""

    instance_id: str | None = None
    access_token: str | None = None
    nexus_base_url: str = "http://localhost:3000"
    ingress_host: str = "127.0.0.1"
    ingress_port: int = 9000
    telemetry_interval_sec: int = 30
    updater_poll_interval_sec: int = 30

    @property
    def is_paired(self) -> bool:
        """是否已完成配对（具备 instance_id 与 access_token）"""
        return bool(self.instance_id and self.access_token)

    @property
    def heartbeat_url(self) -> str:
        """Layer 1 心跳 API 地址"""
        base = self.nexus_base_url.rstrip("/")
        return f"{base}/api/v1/instances/heartbeat"

    def save(self, path: Path | None = None) -> None:
        """持久化配置到文件（仅保存敏感字段）"""
        if not self.instance_id or not self.access_token:
            return
        path = path or _config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "instance_id": self.instance_id,
            "access_token": self.access_token,
            "nexus_base_url": self.nexus_base_url,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)


def load_daemon_config() -> DaemonConfig:
    """
    加载 Daemon 配置

    1. 环境变量：NEXUS_INSTANCE_ID, NEXUS_ACCESS_TOKEN, NEXUS_BASE_URL
    2. 配置文件：~/.jachin/nexus_config.json
    3. core.config.settings（NEXUS_BASE_URL 等）
    """
    cfg = DaemonConfig()

    # 1. 尝试从配置文件加载
    path = _config_path()
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            cfg.instance_id = data.get("instance_id") or cfg.instance_id
            cfg.access_token = data.get("access_token") or cfg.access_token
            cfg.nexus_base_url = data.get("nexus_base_url") or cfg.nexus_base_url
        except Exception:
            pass

    # 2. 环境变量覆盖
    if os.environ.get("NEXUS_INSTANCE_ID"):
        cfg.instance_id = os.environ["NEXUS_INSTANCE_ID"]
    if os.environ.get("NEXUS_ACCESS_TOKEN"):
        cfg.access_token = os.environ["NEXUS_ACCESS_TOKEN"]
    if os.environ.get("NEXUS_BASE_URL"):
        cfg.nexus_base_url = os.environ["NEXUS_BASE_URL"].rstrip("/")

    # 3. Ingress 配置
    if os.environ.get("NEXUS_INGRESS_PORT"):
        try:
            cfg.ingress_port = int(os.environ["NEXUS_INGRESS_PORT"])
        except ValueError:
            pass

    return cfg
