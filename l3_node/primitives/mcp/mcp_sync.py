"""
L3 从 L2 同步 L3_LOCAL MCP 到 ~/.jachin/l3_mcp_cache/

路径 3：L2 同步 L3_LOCAL MCP 到 inventory/l3_mcps/，L3 拉取到 l3_mcp_cache 动态加载。
支持版本比较：manifest.version > local 时删旧拉新。
"""
from __future__ import annotations

import json
import logging
import shutil
import sys
import time
import zipfile
from io import BytesIO
from pathlib import Path

logger = logging.getLogger("l3_node")


def _parse_version(v: str) -> tuple[int, ...]:
    """解析语义化版本为元组。1.2.3 -> (1, 2, 3)"""
    if not v or not isinstance(v, str):
        return (0, 0, 0)
    parts = (v.strip().split("-")[0].split("."))[:3]
    out = []
    for p in parts:
        try:
            out.append(int(p.strip()))
        except ValueError:
            out.append(0)
    while len(out) < 3:
        out.append(0)
    return tuple(out[:3])


def _version_compare(remote: str, local: str) -> int:
    """remote > local 返回 1，相等 0，remote < local 返回 -1"""
    r, l = _parse_version(remote or "0.0.0"), _parse_version(local or "0.0.0")
    return 1 if r > l else (-1 if r < l else 0)

L3_MCP_CACHE = Path.home() / ".jachin" / "l3_mcp_cache"
_GATEWAY_CONFIG = Path.home() / ".jachin" / "l2_gateway_config.json"


def _log(msg: str, err: bool = False) -> None:
    out = sys.stderr if err else sys.stdout
    print(f"[MCP Sync] {msg}", file=out, flush=True)
    (logger.warning if err else logger.info)(msg)


def ensure_mcp_cache_dir() -> Path:
    """确保 l3_mcp_cache 目录存在。"""
    L3_MCP_CACHE.mkdir(parents=True, exist_ok=True)
    return L3_MCP_CACHE


def sync_mcps_from_l2() -> tuple[int, int, list[str]]:
    """
    从 L2 拉取 L3_LOCAL MCP 清单，下载缺失/变更的包到 l3_mcp_cache。
    Returns:
        (synced, skipped, failed)
    """
    _log("开始检查 L2 L3_LOCAL MCP 同步...")
    ensure_mcp_cache_dir()
    _log(f"即将读取 l2_gateway_config path={_GATEWAY_CONFIG}")
    if not _GATEWAY_CONFIG.exists():
        _log("无 l2_gateway_config.json，跳过同步", err=True)
        return 0, 0, []

    try:
        cfg = json.loads(_GATEWAY_CONFIG.read_text(encoding="utf-8"))
    except Exception as e:
        _log(f"解析 l2_gateway_config 失败: {e}", err=True)
        return 0, 0, []

    l2_url = (cfg.get("l2_base_url") or "").strip().rstrip("/")
    sub_account_id = (cfg.get("sub_account_id") or "").strip()
    if not l2_url or not sub_account_id:
        _log("缺少 l2_base_url 或 sub_account_id，跳过同步", err=True)
        return 0, 0, []

    try:
        import httpx
    except ImportError:
        _log("httpx 未安装，跳过同步", err=True)
        return 0, 0, []

    list_url = f"{l2_url}/api/v2/inventory/l3_mcps"
    trigger_url = f"{l2_url}/api/v2/inventory/trigger-sync"
    headers = {"X-Sub-Account-Id": sub_account_id}
    synced, skipped, failed = 0, 0, []

    _log("触发 L2 从 L1 同步...")
    try:
        with httpx.Client(timeout=90.0) as client:
            r = client.post(trigger_url, headers=headers)
            if r.is_success and r.json().get("synced_from_l1"):
                _log("L2 已从 L1 同步完成")
    except Exception as e:
        _log(f"trigger-sync 请求失败: {e}，继续尝试拉取", err=True)

    _log(f"请求 L2 清单: GET {list_url}")
    data: dict = {}
    for attempt in range(3):
        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.get(list_url, headers=headers)
                if resp.status_code == 503 and attempt < 2:
                    _log(f"L2 返回 503（可能启动中），%ds 后重试 (%d/3)", 2, attempt + 1)
                    time.sleep(2)
                    continue
                if not resp.is_success:
                    _log(f"L2 清单请求失败 {resp.status_code}", err=True)
                    return 0, 0, []
                data = resp.json()
                break
        except Exception as e:
            if attempt < 2:
                _log(f"请求 L2 失败，重试: {e}")
                time.sleep(2)
            else:
                _log(f"请求 L2 失败: {e}", err=True)
                return 0, 0, []

    mcps = data.get("mcps") or []
    if not mcps:
        _log("L2 无 L3_LOCAL MCP，跳过")
        return 0, 0, []

    _log(f"清单拉取完成 count={len(mcps)} 即将同步到 cache={L3_MCP_CACHE}")
    for mcp in mcps:
        item_id = mcp.get("item_id") or mcp.get("id", "")
        if not item_id:
            continue
        cache_dir = L3_MCP_CACHE / item_id
        plugin_path = cache_dir / "plugin.json"
        remote_version = mcp.get("version") or "1.0.0"
        local_version = None
        if plugin_path.exists():
            _log(f"即将读取 item_id={item_id} 本地版本 path={plugin_path}")
            try:
                pj = json.loads(plugin_path.read_text(encoding="utf-8"))
                local_version = pj.get("version")
            except Exception:
                pass
        need_download = not plugin_path.exists() or _version_compare(remote_version, local_version or "0") > 0
        if not need_download:
            skipped += 1
            continue

        download_url = f"{l2_url}/api/v2/inventory/l3_mcps/{item_id}/download"
        _log(f"即将下载 item_id={item_id} name={mcp.get('name', '')} url={download_url}")
        try:
            with httpx.Client(timeout=120.0) as client:
                r = client.get(download_url, headers=headers)
                if not r.is_success:
                    _log(f"下载失败 item_id={item_id} HTTP {r.status_code}", err=True)
                    failed.append(f"{item_id}: HTTP {r.status_code}")
                    continue
                zip_data = r.content
                if cache_dir.exists():
                    _log(f"即将删除旧版本 item_id={item_id} cache_dir={cache_dir}")
                    shutil.rmtree(cache_dir, ignore_errors=True)
                cache_dir.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(BytesIO(zip_data), "r") as zf:
                    zf.extractall(cache_dir)
                # 075: 按 config/manifest.yaml 写出配置到 ~/.jachin/config/
                try:
                    from l3_node.config_writeout import write_config_from_package
                    write_config_from_package(cache_dir, item_id)
                except Exception as cfg_err:
                    _log(f"配置写出失败 {item_id}: {cfg_err}", err=True)
                synced += 1
                _log(f"拉取成功 item_id={item_id} name={mcp.get('name', '')}")
        except Exception as e:
            failed.append(f"{item_id}: {e}")
            _log(f"下载失败 item_id={item_id} err={e}", err=True)

    _log(f"同步完成: synced={synced} skipped={skipped} failed={len(failed)}")
    return synced, skipped, failed
