"""
Jachin Nexus V2 - L2 本地数字仓库扫描器

扫描 ~/.jachin/inventory/ 下的侧载技能与 MCP 配置，
动态注入 MCPManager，缓存 Wasm 技能元数据。
侧载时自动生成 .local_meta 结构化元数据。
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

# 侧载标识：无 .sync_meta 表示非 L1 同步而来
LOCAL_META_FILENAME = ".local_meta"
SYNC_META_FILENAME = ".sync_meta"

logger = logging.getLogger(__name__)


def _compute_wasm_sha256(wasm_path: Path) -> str:
    """计算 Wasm 文件的 SHA-256 哈希，供 L3 下载校验。"""
    h = hashlib.sha256()
    with open(wasm_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest().lower()


def _read_wasm_sha256_from_meta(meta_path: Path) -> str | None:
    """从 .sync_meta 或 .local_meta 读取 wasm_sha256（若存在）。"""
    if not meta_path.exists():
        return None
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        return data.get("wasm_sha256") or None
    except Exception:
        return None


def _ensure_local_meta(subdir: Path, plugin_id: str, wasm_sha256: str | None = None) -> None:
    """
    侧载目录（无 .sync_meta）时，生成或更新 .local_meta。
    记录 origin: SIDE_LOAD、installed_at、is_private: true、wasm_sha256（可选）。
    """
    sync_meta = subdir / SYNC_META_FILENAME
    if sync_meta.exists():
        return  # L1 同步而来，不写 .local_meta
    local_meta_path = subdir / LOCAL_META_FILENAME
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    meta: dict[str, Any] = {
        "origin": "SIDE_LOAD",
        "installed_at": now_iso,
        "is_private": True,
        "plugin_id": plugin_id,
    }
    if wasm_sha256:
        meta["wasm_sha256"] = wasm_sha256
    try:
        if local_meta_path.exists():
            existing = json.loads(local_meta_path.read_text(encoding="utf-8"))
            meta["installed_at"] = existing.get("installed_at", meta["installed_at"])
        local_meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.debug("[Inventory] 侧载元数据已写入 path=%s", local_meta_path)
    except Exception as e:
        logger.warning("[Inventory] 写入 .local_meta 失败 path=%s err=%s", subdir.name, e)

# 仓库根目录
INVENTORY_ROOT = Path.home() / ".jachin" / "inventory"
SKILLS_DIR = INVENTORY_ROOT / "skills"
MCPS_DIR = INVENTORY_ROOT / "mcps"
L3_MCPS_DIR = INVENTORY_ROOT / "l3_mcps"  # 路径 3：L3_LOCAL MCP，L3 拉取后动态加载

# 项目根目录（用于 MCP config 中 __PROJECT_ROOT__ 占位符替换）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 本地 Wasm 技能缓存：skill_id -> {id, name, description, permissions, wasm_path, ...}
registered_local_skills: dict[str, dict[str, Any]] = {}


def _prune_mcp_filesystem_roots(resolved: list[Any]) -> list[Any] | None:
    """
    @modelcontextprotocol/server-filesystem 若传入不存在的目录，子进程会立刻退出，
    Python 端表现为 initialize 时 Connection closed。
    返回 None 表示无任何有效根目录，调用方应跳过该 MCP。
    """
    if not isinstance(resolved, list):
        return resolved
    try:
        pkg_idx = next(
            i
            for i, a in enumerate(resolved)
            if isinstance(a, str) and "server-filesystem" in a
        )
    except StopIteration:
        return resolved
    head = resolved[: pkg_idx + 1]
    roots = resolved[pkg_idx + 1 :]
    good: list[Any] = []
    for r in roots:
        if isinstance(r, str) and Path(r).is_dir():
            good.append(r)
        elif isinstance(r, str):
            logger.warning(
                "[Inventory] server-filesystem 跳过不存在的根路径（避免 MCP 子进程立即退出）: %s",
                r,
            )
    if not good:
        return None
    return head + good


def ensure_inventory_dirs() -> None:
    """确保仓库目录存在。"""
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    MCPS_DIR.mkdir(parents=True, exist_ok=True)
    L3_MCPS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        from core.skill_registry import ensure_volumes_root
        ensure_volumes_root()
    except Exception:
        pass
    logger.debug("[Inventory] 目录已就绪 skills=%s mcps=%s", SKILLS_DIR, MCPS_DIR)


def _extract_mcp_configs(data: dict[str, Any]) -> list[dict[str, Any]]:
    """
    从 JSON 数据提取 MCP 配置列表。
    支持 mcp_servers (list/dict)、mcpServers (object)、单条 command 配置。
    """
    configs: list[dict[str, Any]] = []
    if "mcp_servers" in data:
        lst = data.get("mcp_servers")
        if isinstance(lst, list):
            configs = lst
        elif isinstance(lst, dict):
            configs = [lst]
    elif "mcpServers" in data:
        # 格式：{ "mcpServers": { "server_id": { "command", "args" } } }
        servers = data.get("mcpServers")
        if isinstance(servers, dict):
            for sid, scfg in servers.items():
                if isinstance(scfg, dict) and scfg.get("command"):
                    cfg = dict(scfg)
                    cfg["id"] = cfg.get("id") or cfg.get("name") or sid
                    # 替换 args 中的 __PROJECT_ROOT__ 为实际项目根路径
                    args = cfg.get("args") or []
                    if isinstance(args, list):
                        resolved = []
                        for a in args:
                            if isinstance(a, str) and "__PROJECT_ROOT__" in a:
                                sub = a.replace("__PROJECT_ROOT__/", "").replace("__PROJECT_ROOT__", "").lstrip("/")
                                resolved.append(str(_PROJECT_ROOT / sub) if sub else str(_PROJECT_ROOT))
                            else:
                                resolved.append(a)
                        pruned = _prune_mcp_filesystem_roots(resolved)
                        if pruned is None:
                            logger.warning(
                                "[Inventory] 跳过 MCP server_id=%s：server-filesystem 无有效根目录",
                                sid,
                            )
                            continue
                        cfg["args"] = pruned
                    configs.append(cfg)
    elif data.get("command"):
        configs = [data]
    return configs


async def scan_local_mcps() -> int:
    """
    遍历 ~/.jachin/inventory/mcps/ 下的 .json 文件及子目录（含 plugin.json + config.json），
    校验格式，将发现的 MCP 配置注入 MCPManager 并拉起。
    返回成功注入的 Server 数量。
    """
    if not MCPS_DIR.exists():
        logger.debug("[Inventory] MCP 目录不存在 path=%s", MCPS_DIR)
        return 0

    try:
        from core.mcp_client import get_mcp_manager
        manager = get_mcp_manager()
    except ImportError as e:
        logger.warning("[Inventory] MCPManager 不可用，跳过 MCP 扫描: %s", e)
        return 0

    count = 0

    # 1. 扁平 .json 文件（兼容旧格式）
    for p in MCPS_DIR.iterdir():
        if not p.is_file() or p.suffix.lower() != ".json":
            continue
        try:
            raw = p.read_text(encoding="utf-8")
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.warning("[Inventory] MCP 配置 JSON 解析失败 file=%s err=%s，跳过", p.name, e)
            continue
        except Exception as e:
            logger.warning("[Inventory] 读取 MCP 配置失败 file=%s err=%s，跳过", p.name, e)
            continue

        configs = _extract_mcp_configs(data) if isinstance(data, dict) else []
        if isinstance(data, list):
            configs = data

        for cfg in configs:
            if not isinstance(cfg, dict):
                continue
            if not cfg.get("command"):
                logger.warning("[Inventory] MCP 配置缺少 command file=%s，跳过", p.name)
                continue
            try:
                ok = await manager.add_server(cfg)
                if ok:
                    count += 1
                    logger.info("[Inventory] MCP 侧载挂载成功 file=%s server_id=%s", p.name, cfg.get("id", cfg.get("name")))
            except Exception as e:
                logger.warning("[Inventory] MCP 注入失败 file=%s cfg=%s err=%s，跳过", p.name, cfg.get("id"), e)

    # 2. 子目录结构：local-hr-fs/ 含 plugin.json + config.json
    for subdir in MCPS_DIR.iterdir():
        if not subdir.is_dir():
            continue
        config_path = subdir / "config.json"
        plugin_path = subdir / "plugin.json"
        if not config_path.exists():
            continue
        try:
            raw = config_path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.warning("[Inventory] MCP config.json 解析失败 dir=%s err=%s，跳过", subdir.name, e)
            continue
        except Exception as e:
            logger.warning("[Inventory] 读取 MCP config.json 失败 dir=%s err=%s，跳过", subdir.name, e)
            continue

        configs = _extract_mcp_configs(data) if isinstance(data, dict) else []
        for cfg in configs:
            if not isinstance(cfg, dict):
                continue
            if not cfg.get("command"):
                logger.warning("[Inventory] MCP 配置缺少 command dir=%s，跳过", subdir.name)
                continue
            try:
                ok = await manager.add_server(cfg)
                if ok:
                    count += 1
                    logger.info("[Inventory] MCP 侧载挂载成功 dir=%s server_id=%s", subdir.name, cfg.get("id", cfg.get("name")))
            except Exception as e:
                logger.warning("[Inventory] MCP 注入失败 dir=%s cfg=%s err=%s，跳过", subdir.name, cfg.get("id"), e)

    logger.info("[Inventory] MCP 扫描完成 注入=%d", count)
    return count


async def scan_local_skills() -> int:
    """
    遍历 ~/.jachin/inventory/skills/ 目录，
    寻找包含 plugin.json 和 .wasm 的子目录，
    提取技能元数据并缓存在 registered_local_skills。
    返回发现的技能数量。
    """
    global registered_local_skills
    found: dict[str, dict[str, Any]] = {}

    if not SKILLS_DIR.exists():
        logger.debug("[Inventory] Skills 目录不存在 path=%s", SKILLS_DIR)
        registered_local_skills.clear()
        return 0

    for subdir in SKILLS_DIR.iterdir():
        if not subdir.is_dir():
            continue
        plugin_path = subdir / "plugin.json"
        if not plugin_path.exists():
            logger.debug("[Inventory] 子目录缺少 plugin.json path=%s，跳过", subdir.name)
            continue

        wasm_files = list(subdir.glob("*.wasm"))
        if not wasm_files:
            logger.warning("[Inventory] 子目录无 .wasm 文件 path=%s，跳过", subdir.name)
            continue

        try:
            desc = json.loads(plugin_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            logger.warning("[Inventory] plugin.json 解析失败 path=%s err=%s，跳过", subdir.name, e)
            continue
        except Exception as e:
            logger.warning("[Inventory] 读取 plugin.json 失败 path=%s err=%s，跳过", subdir.name, e)
            continue

        plugin_id = desc.get("id") or subdir.name
        entry = desc.get("entry", "main.wasm")
        wasm_path = subdir / entry
        if not wasm_path.exists():
            wasm_path = wasm_files[0]

        skill_id = f"jpp:{plugin_id}"
        params = desc.get("parameters", desc.get("schema", {}).get("input", {}))
        if isinstance(params, dict):
            param_names = list(params.keys()) if params else []
        elif isinstance(params, list):
            param_names = [x.get("name", x) if isinstance(x, dict) else str(x) for x in params]
        else:
            param_names = ["input"]

        # item_id：目录名，供 L3 下载接口使用；sha256：供 L3 校验
        item_id = subdir.name
        sync_meta_path = subdir / SYNC_META_FILENAME
        local_meta_path = subdir / LOCAL_META_FILENAME
        sha256_val = _read_wasm_sha256_from_meta(sync_meta_path) or _read_wasm_sha256_from_meta(local_meta_path)
        if not sha256_val:
            sha256_val = _compute_wasm_sha256(wasm_path)

        # 侧载目录：无 .sync_meta 时生成 .local_meta，并标记 origin / is_private
        is_side_load = not sync_meta_path.exists()
        if is_side_load:
            _ensure_local_meta(subdir, plugin_id, sha256_val)

        found[skill_id] = {
            "id": skill_id,
            "item_id": item_id,
            "name": desc.get("name", plugin_id),
            "version": desc.get("version", "1.0.0"),
            "description": desc.get("description", ""),
            "permissions": desc.get("permissions", []),
            "wasm_path": str(wasm_path.resolve()),
            "params": param_names,
            "source": "inventory",
            "sha256": sha256_val,
            "entry": wasm_path.name,
            "origin": "SIDE_LOAD" if is_side_load else "L1_SYNC",
            "is_private": is_side_load,
        }
        # Jachin 注册表 + 数据卷：解析 configs/volumes 声明，写入默认值并创建卷
        configs = desc.get("configs")
        volumes = desc.get("volumes")
        if configs or volumes:
            try:
                from core.skill_registry import setup_skill_registry_and_volumes
                setup_skill_registry_and_volumes(skill_id, item_id, configs, volumes)
            except Exception as e:
                logger.warning("[Inventory] 注册表/数据卷设置失败 skill=%s err=%s", item_id, e)
        logger.debug("[Inventory] 发现技能 id=%s path=%s", skill_id, subdir.name)

    registered_local_skills.clear()
    registered_local_skills.update(found)
    logger.info("[Inventory] Skills 扫描完成 count=%d", len(registered_local_skills))
    return len(registered_local_skills)


async def reload_inventory() -> dict[str, Any]:
    """
    热重载：重新扫描 MCP 与 Skills。
    注意：MCP 侧载会追加到现有 MCPManager，不会清空已有 Server。
    Skills 会全量替换 registered_local_skills。
    完成后触发 INVENTORY_UPDATED 事件，通过 SSE 推送给 L3 客户端。
    """
    ensure_inventory_dirs()
    mcp_count = await scan_local_mcps()
    skills_count = await scan_local_skills()
    result = {
        "ok": True,
        "mcps_injected": mcp_count,
        "skills_found": skills_count,
    }
    # 触发 UI 同步事件，L3 客户端通过 SSE 收到后刷新技能面板
    try:
        from core.events import emit_ui_sync_event
        emit_ui_sync_event(
            "INVENTORY_UPDATED",
            "新技能已就绪",
            mcps_injected=mcp_count,
            skills_found=skills_count,
        )
    except Exception:
        pass
    return result
