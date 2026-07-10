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
# 打包后可修改；桌面端「设置 → 飞书长连接」可切换本机是否接管

im_channels:
  # 主机器人 IM（PMO / 通用 / 招聘路由 — 同一长连接内按消息分流）
  lark:
    enabled: false
    mode: long_connection
    app_id: ""              # 空则读 LARK_APP_ID / FEISHU_APP_ID
    app_secret: ""
    # 多机共享：本节点只处理这些 chat_id；空=处理全部推到本连接的会话
    chat_ids: []
    domain: "https://open.feishu.cn"

  # HR 招聘专用机器人（与上不同 app 时单独开；同 app 则只开 lark 即可）
  lark_hr:
    enabled: false
    mode: long_connection
    app_id: ""              # 空则读 HR_LARK_APP_ID → LARK_APP_ID
    app_secret: ""
    chat_ids: []
    domain: "https://open.feishu.cn"

  # PMO 多维表变更事件（drive.file.bitable_record_changed_v1，非 IM 聊天）
  lark_pmo_bitable:
    enabled: false
    app_id: ""              # 空则读 pmo_bitable_watch.yaml / PMO_BITABLE_WATCH_*
    app_secret: ""
    domain: "https://open.feishu.cn"

  telegram:
    enabled: false
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
