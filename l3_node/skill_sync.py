"""
L3 启动时从 L2 同步技能到 ~/.jachin/l3_skill_cache/

当 l2_gateway_config.json 含 sub_account_id 时，在 L3 获批后自动执行。
不依赖 Desktop 的 perform_startup_sync，确保 L3 独立启动时也能拿到技能。

注意：当前 L2 /skills/{item_id}/download 仅返回单个 wasm 文件，不包含完整 JSP 包及 config。
若将来 L2 改为下发完整技能 zip，可在此处解压后调用 config_manifest.write_config_from_manifest()
实现技能配置随包写出，与 MCP 路径一致。
"""
from __future__ import annotations

import hashlib
import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger("l3_node")

_L3_CACHE = Path.home() / ".jachin" / "l3_skill_cache"
_GATEWAY_CONFIG = Path.home() / ".jachin" / "l2_gateway_config.json"


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


def _log(msg: str, err: bool = False) -> None:
    """同时输出到 logger 和控制台，确保 PowerShell 可见"""
    out = sys.stderr if err else sys.stdout
    print(f"[SkillSync] {msg}", file=out, flush=True)
    (logger.warning if err else logger.info)(msg)


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().lower()


def sync_skills_from_l2() -> tuple[int, int, list[str]]:
    """
    从 L2 拉取技能清单，下载缺失/变更的 Wasm 到 l3_skill_cache。
    Returns:
        (synced, skipped, failed)
    """
    _log("开始检查 L2 技能同步...")
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
    _log(f"配置: l2_url={l2_url} sub_account_id={sub_account_id[:16]}...")
    if not l2_url or not sub_account_id:
        _log("缺少 l2_base_url 或 sub_account_id，跳过同步", err=True)
        return 0, 0, []

    try:
        import httpx
    except ImportError:
        _log("httpx 未安装，跳过同步", err=True)
        return 0, 0, []

    list_url = f"{l2_url}/api/v2/inventory/skills"
    trigger_url = f"{l2_url}/api/v2/inventory/trigger-sync"
    headers = {"X-Sub-Account-Id": sub_account_id}
    synced, skipped, failed = 0, 0, []

    # L3 启动时先触发 L2 从 L1 同步，确保武库最新
    _log("触发 L2 从 L1 同步...")
    try:
        with httpx.Client(timeout=90.0) as client:
            r = client.post(trigger_url, headers=headers)
            if r.is_success:
                data_trigger = r.json()
                if data_trigger.get("synced_from_l1"):
                    _log("L2 已从 L1 同步完成")
                else:
                    _log("L2 未配对 L1 或已是最新，继续拉取")
            else:
                _log(f"trigger-sync 失败 {r.status_code}，继续尝试拉取", err=True)
    except Exception as e:
        _log(f"trigger-sync 请求失败: {e}，继续尝试拉取", err=True)

    _log(f"请求 L2 清单: GET {list_url}")
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(list_url, headers=headers)
            _log(f"L2 响应: status={resp.status_code} body_len={len(resp.content)}")
            if not resp.is_success:
                _log(f"L2 清单请求失败 {resp.status_code}: {resp.text[:300]}", err=True)
                return 0, 0, []
            data = resp.json()
    except Exception as e:
        _log(f"请求 L2 失败: {e}", err=True)
        return 0, 0, []

    skills = data.get("skills") or data.get("manifest") or []
    _log(f"L2 skills count={len(skills)} keys={list(data.keys())}")
    if skills:
        for i, s in enumerate(skills[:5]):
            _log(f"  技能[{i}]: item_id={s.get('item_id')} name={s.get('name')}")
    if not skills:
        sample = str(data)[:200] if isinstance(data, dict) else str(data)[:200]
        _log(f"L2 empty. sample={sample}", err=True)
        _log("Fix: 1) L2 admin http://localhost:18888/admin/ click Sync 2) Or run .\\scripts\\diagnose-skill-sync.ps1", err=True)
        return 0, 0, []

    _log(f"清单拉取完成 count={len(skills)} 即将同步到 cache={_L3_CACHE}")
    _L3_CACHE.mkdir(parents=True, exist_ok=True)

    for skill in skills:
        item_id = skill.get("item_id") or skill.get("id", "").replace("jpp:", "")
        if not item_id:
            continue
        entry = skill.get("entry") or "main.wasm"
        skill_dir = _L3_CACHE / item_id
        wasm_path = skill_dir / entry
        expected_sha = (skill.get("sha256") or skill.get("wasm_sha256") or "").strip().lower()

        remote_version = skill.get("version") or "1.0.0"
        local_version = None
        plugin_path = skill_dir / "plugin.json"
        if plugin_path.exists():
            _log(f"即将读取 item_id={item_id} 本地版本 path={plugin_path}")
            try:
                pj = json.loads(plugin_path.read_text(encoding="utf-8"))
                local_version = pj.get("version")
            except Exception:
                pass
        need_download = True
        if wasm_path.exists():
            if _version_compare(remote_version, local_version or "0") > 0:
                need_download = True
            elif expected_sha:
                _log(f"即将校验 SHA256 item_id={item_id} path={wasm_path}")
                actual = _sha256_hex(wasm_path.read_bytes())
                if actual == expected_sha:
                    need_download = False
                    skipped += 1
            else:
                need_download = False
                skipped += 1

        if not need_download:
            continue

        download_url = f"{l2_url}/api/v2/inventory/skills/{item_id}/download"
        _log(f"即将下载 item_id={item_id} name={skill.get('name', '')} url={download_url}")
        try:
            with httpx.Client(timeout=120.0) as client:
                r = client.get(download_url, headers=headers)
                if not r.is_success:
                    _log(f"下载失败 item_id={item_id} HTTP {r.status_code}", err=True)
                    failed.append(f"{item_id}: HTTP {r.status_code}")
                    continue
                bytes_data = r.content
                if expected_sha:
                    actual = _sha256_hex(bytes_data)
                    if actual != expected_sha:
                        failed.append(f"{item_id}: SHA256 mismatch")
                        continue
                if skill_dir.exists():
                    import shutil
                    _log(f"即将删除旧版本 item_id={item_id} skill_dir={skill_dir}")
                    shutil.rmtree(skill_dir, ignore_errors=True)
                skill_dir.mkdir(parents=True, exist_ok=True)
                wasm_path.write_bytes(bytes_data)
                plugin_id = skill.get("id", "").replace("jpp:", "") or item_id
                plugin_json = {
                    "id": plugin_id,
                    "name": skill.get("name", plugin_id),
                    "description": skill.get("description", ""),
                    "entry": entry,
                    "version": remote_version,
                    "parameters": [{"name": p} for p in (skill.get("params") or ["input"])],
                }
                (skill_dir / "plugin.json").write_text(
                    json.dumps(plugin_json, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                synced += 1
                _log(f"拉取成功 item_id={item_id} name={skill.get('name', '')}")
        except Exception as e:
            failed.append(f"{item_id}: {e}")
            _log(f"下载失败 item_id={item_id} err={e}", err=True)

    _log(f"同步完成: synced={synced} skipped={skipped} failed={len(failed)}")
    if failed:
        for f in failed[:5]:
            _log(f"  失败: {f}", err=True)
    return synced, skipped, failed
