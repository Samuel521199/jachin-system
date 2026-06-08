"""IM 长连接凭证解析 — im_channels.yaml 与环境变量回落。"""
from __future__ import annotations

import os
from typing import Any


def resolve_lark_im_credentials(
    config: dict[str, Any],
    channel_id: str,
) -> tuple[str, str]:
    """
    解析 Lark IM 入站长连接凭证。

    - ``lark``: yaml app_id/secret → ``LARK_APP_ID`` / ``FEISHU_APP_ID``
    - ``lark_hr``: yaml → ``HR_LARK_APP_ID`` → 通用 ``resolve_lark_credentials()``
    """
    cid = (channel_id or "lark").strip() or "lark"
    yaml_id = str(config.get("app_id") or "").strip()
    yaml_sec = str(config.get("app_secret") or "").strip()
    if yaml_id and yaml_sec:
        return yaml_id, yaml_sec

    if cid == "lark_hr":
        from l3_node.channels.lark.client import resolve_hr_lark_credentials

        creds = resolve_hr_lark_credentials()
        return creds[0], creds[1]

    from l3_node.channels.lark.client import resolve_lark_credentials

    return resolve_lark_credentials()


def resolve_pmo_bitable_credentials(config: dict[str, Any]) -> tuple[str, str]:
    """PMO 多维表变更长连接：yaml → pmo_bitable_watch.yaml → PMO_BITABLE_WATCH_* / LARK_*。"""
    yaml_id = str(config.get("app_id") or "").strip()
    yaml_sec = str(config.get("app_secret") or "").strip()
    if yaml_id and yaml_sec:
        return yaml_id, yaml_sec

    try:
        from l3_node.tools.pmo_bitable_watch import _load_watch_config

        cfg = _load_watch_config()
        app_id = str(cfg.get("app_id") or "").strip()
        app_secret = str(cfg.get("app_secret") or "").strip()
        if app_id and app_secret:
            return app_id, app_secret
    except Exception:
        pass

    app_id = (
        (os.environ.get("PMO_BITABLE_WATCH_APP_ID") or "").strip()
        or (os.environ.get("LARK_APP_ID") or "").strip()
        or (os.environ.get("FEISHU_APP_ID") or "").strip()
    )
    app_secret = (
        (os.environ.get("PMO_BITABLE_WATCH_APP_SECRET") or "").strip()
        or (os.environ.get("LARK_APP_SECRET") or "").strip()
        or (os.environ.get("FEISHU_APP_SECRET") or "").strip()
    )
    return app_id, app_secret
