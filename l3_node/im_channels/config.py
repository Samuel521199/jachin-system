"""
IM 通道配置 — 从 ~/.jachin/config/im_channels.yaml 加载

支持打包后修改，Lark/Telegram 等同维度配置。
多机共享飞书应用时，通过 chat_ids 指定本节点处理的会话。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

def _get_jachin_root() -> Path:
    home = os.environ.get("JACHIN_HOME")
    if home:
        return Path(home).expanduser().resolve()
    return Path.home() / ".jachin"


_CONFIG_EXAMPLE = """# IM 通道配置 — L3 独立使用 Lark/Telegram 等，无需 Layer 1
# 路径: ~/.jachin/config/im_channels.yaml（支持 JACHIN_HOME 覆盖）
# 打包后可修改

im_channels:
  lark:
    enabled: true
    mode: long_connection   # long_connection | webhook
    app_id: ""              # 飞书应用 App ID (cli_xxx)
    app_secret: ""          # 飞书应用 App Secret
    # 多机共享：本节点只处理这些 chat_id，空则处理全部
    # 非空时，仅处理列表中的会话，避免回复到其他机器
    chat_ids: []
    # 机器人 WebSocket 须与应用创建平台一致。国际版默认 larksuite；仅「飞书中国自建应用 + 入耳长连接」时改为
    # https://open.feishu.cn 并在环境变量设 LARK_USE_FEISHU=1（勿与仅用于巡检 Open API 的 FEISHU_* 混推域名）
    domain: "https://open.larksuite.com"
  telegram:
    enabled: false
    # bot_token: ""
    # future
"""


def get_config_path() -> Path:
    """配置路径，支持 JACHIN_HOME"""
    return _get_jachin_root() / "config" / "im_channels.yaml"


def load_config() -> dict[str, Any]:
    """
    加载 im_channels 配置。
    若文件不存在，返回空配置（所有通道 disabled）。
    """
    path = get_config_path()
    if not path.exists():
        logger.debug("[IM Channels] 配置不存在 %s，使用空配置", path)
        return {"im_channels": {}}

    try:
        import yaml
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {"im_channels": {}}
        return raw
    except Exception as e:
        logger.warning("[IM Channels] 配置解析失败 %s: %s", path, e)
        return {"im_channels": {}}


def get_channel_config(channel_id: str) -> dict[str, Any] | None:
    """获取单个通道配置，未启用或不存在返回 None"""
    cfg = load_config()
    channels = cfg.get("im_channels") or {}
    ch = channels.get(channel_id)
    if not isinstance(ch, dict) or not ch.get("enabled", False):
        return None
    return ch


def ensure_config_dir() -> Path:
    """确保配置目录存在，若不存在则写出示例"""
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(_CONFIG_EXAMPLE, encoding="utf-8")
        logger.info("[IM Channels] 已创建示例配置 %s", path)
    return path
