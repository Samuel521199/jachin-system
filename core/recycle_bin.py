"""
技能回收站：软删除、恢复、彻底删除

- 技能列表删除 -> 移入回收站（软删除）
- 回收站恢复 -> 移回 inventory
- 回收站删除 -> 彻底删除
"""
from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any

from core.inventory_scanner import SKILLS_DIR
from core.skill_registry import (
    UNINSTALLED_BUILTIN_PATH,
    cleanup_builtin_skill_artifacts,
    get_skill_config,
    update_skill_config,
)

logger = logging.getLogger(__name__)

RECYCLE_BIN_ROOT = Path.home() / ".jachin" / "recycle_bin"
_L3_CACHE_DIR = Path.home() / ".jachin" / "l3_skill_cache"
_WASM_PLUGINS_DIR = Path(__file__).resolve().parent.parent / "l3_node" / "skills" / "wasm_plugins"
_PROJ_ROOT = Path(__file__).resolve().parent.parent
_JD_DIR = _PROJ_ROOT / "config" / "hr_jds"


def _ensure_recycle_bin() -> None:
    RECYCLE_BIN_ROOT.mkdir(parents=True, exist_ok=True)


def _find_skill_source(item_id: str) -> tuple[str, Path | None]:
    """
    查找技能来源。返回 (source, path)。
    source: "inventory" | "cache" | "builtin"
    """
    inv_path = SKILLS_DIR / item_id
    if inv_path.exists() and inv_path.is_dir():
        return "inventory", inv_path
    cache_path = _L3_CACHE_DIR / item_id
    if cache_path.exists() and cache_path.is_dir():
        return "cache", cache_path
    builtin_path = _WASM_PLUGINS_DIR / item_id
    if builtin_path.exists() and builtin_path.is_dir():
        return "builtin", builtin_path
    return "unknown", None


def _read_skill_name(item_id: str, skill_dir: Path) -> str:
    """从 plugin.json 读取技能名称"""
    plugin_path = skill_dir / "plugin.json"
    if plugin_path.exists():
        try:
            data = json.loads(plugin_path.read_text(encoding="utf-8"))
            return data.get("name") or data.get("id") or item_id
        except Exception:
            pass
    return item_id


def _make_recycle_id(item_id: str) -> str:
    """生成回收站内唯一目录名"""
    ts = int(time.time() * 1000)
    return f"{item_id}_{ts}"


def move_to_recycle_bin(item_id: str, purge_data: bool = False) -> dict[str, Any]:
    """
    将技能移入回收站（软删除）。
    返回 { ok, item_id, recycle_id, source, error? }
    """
    _ensure_recycle_bin()
    source, src_path = _find_skill_source(item_id)
    if not src_path or not src_path.exists():
        return {"ok": False, "item_id": item_id, "error": f"未找到技能 item_id={item_id}"}

    name = _read_skill_name(item_id, src_path)
    recycle_id = _make_recycle_id(item_id)
    dest_dir = RECYCLE_BIN_ROOT / recycle_id
    dest_dir.mkdir(parents=True, exist_ok=True)

    meta: dict[str, Any] = {
        "item_id": item_id,
        "skill_id": f"jpp:{item_id}" if "com.jachin" in item_id else f"jpp:{item_id}",
        "name": name,
        "source": source,
        "deleted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    try:
        # 导出 skill_registry 配置（所有来源均保存，恢复时完整还原）
        try:
            cfg = get_skill_config(item_id)
            if cfg and isinstance(cfg, dict):
                cfg_export = {k: v for k, v in cfg.items() if not (k and str(k).startswith("_"))}
                if cfg_export:
                    (dest_dir / "registry_config.json").write_text(
                        json.dumps(cfg_export, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
        except Exception as e:
            logger.debug("[RecycleBin] 导出 registry 配置失败 item_id=%s err=%s", item_id, e)

        if source in ("inventory", "cache"):
            # 移动整个目录
            shutil.move(str(src_path), str(dest_dir / "skill"))
        else:
            # builtin: 复制（项目目录只读）
            shutil.copytree(str(src_path), str(dest_dir / "skill"))
            # 复制 JD 配置文件（若有，与 registry 互为备份）
            for jd_name in (f"{item_id}.md", f"{item_id.replace('-', '_')}.md"):
                jd_file = _JD_DIR / jd_name
                if jd_file.exists():
                    (dest_dir / "jd_config.md").write_text(jd_file.read_text(encoding="utf-8"), encoding="utf-8")
                    break
            # 加入已卸载列表，清理 registry
            cleanup_builtin_skill_artifacts(item_id)

        meta["skill_id"] = _infer_skill_id(item_id, dest_dir / "skill")
        meta_path = dest_dir / "meta.json"
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        # 若 L3 缓存中也有该技能，一并移除（避免技能仍出现在列表中）
        cache_path = _L3_CACHE_DIR / item_id
        if cache_path.exists() and cache_path.is_dir():
            try:
                shutil.rmtree(cache_path)
                logger.info("[RecycleBin] 已清理 L3 缓存 item_id=%s", item_id)
            except Exception as e:
                logger.debug("[RecycleBin] 清理 L3 缓存失败 item_id=%s err=%s", item_id, e)

        logger.info("[RecycleBin] 已移入回收站 item_id=%s source=%s recycle_id=%s", item_id, source, recycle_id)
        return {"ok": True, "item_id": item_id, "recycle_id": recycle_id, "source": source, "name": name}
    except Exception as e:
        logger.warning("[RecycleBin] 移入回收站失败 item_id=%s err=%s", item_id, e)
        if dest_dir.exists():
            shutil.rmtree(dest_dir, ignore_errors=True)
        return {"ok": False, "item_id": item_id, "error": str(e)}


def _infer_skill_id(item_id: str, skill_dir: Path) -> str:
    plugin_path = skill_dir / "plugin.json"
    if plugin_path.exists():
        try:
            data = json.loads(plugin_path.read_text(encoding="utf-8"))
            pid = data.get("id")
            if pid:
                return f"jpp:{pid}" if not pid.startswith("jpp:") else pid
        except Exception:
            pass
    return f"jpp:{item_id}"


def list_recycle_bin() -> list[dict[str, Any]]:
    """列出回收站中的技能"""
    _ensure_recycle_bin()
    items: list[dict[str, Any]] = []
    for d in RECYCLE_BIN_ROOT.iterdir():
        if not d.is_dir():
            continue
        meta_path = d / "meta.json"
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            items.append({
                "recycle_id": d.name,
                "item_id": meta.get("item_id", d.name.split("_")[0]),
                "skill_id": meta.get("skill_id", ""),
                "name": meta.get("name", d.name),
                "source": meta.get("source", "unknown"),
                "deleted_at": meta.get("deleted_at", ""),
            })
        except Exception as e:
            logger.debug("[RecycleBin] 读取 meta 失败 path=%s err=%s", meta_path, e)
    items.sort(key=lambda x: x.get("deleted_at", ""), reverse=True)
    return items


def restore_from_recycle_bin(recycle_id: str) -> dict[str, Any]:
    """
    从回收站恢复技能到 inventory。
    返回 { ok, item_id, error? }
    """
    _ensure_recycle_bin()
    item_dir = RECYCLE_BIN_ROOT / recycle_id
    if not item_dir.exists() or not item_dir.is_dir():
        return {"ok": False, "recycle_id": recycle_id, "error": "回收站项不存在"}

    meta_path = item_dir / "meta.json"
    if not meta_path.exists():
        return {"ok": False, "recycle_id": recycle_id, "error": "meta.json 缺失"}

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        item_id = meta.get("item_id", recycle_id.split("_")[0])
    except Exception:
        item_id = recycle_id.split("_")[0]

    skill_src = item_dir / "skill"
    if not skill_src.exists():
        return {"ok": False, "recycle_id": recycle_id, "item_id": item_id, "error": "skill 目录缺失"}

    dest = SKILLS_DIR / item_id
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    shutil.move(str(skill_src), str(dest))

    # 同步到 L3 缓存，否则 L3 技能列表不会显示（L3 从 l3_skill_cache 读取，不从 inventory）
    cache_dest = _L3_CACHE_DIR / item_id
    if not cache_dest.exists() or not (cache_dest / "plugin.json").exists():
        try:
            _L3_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            if cache_dest.exists():
                shutil.rmtree(cache_dest, ignore_errors=True)
            shutil.copytree(str(dest), str(cache_dest))
            logger.info("[RecycleBin] 已同步到 L3 缓存 item_id=%s", item_id)
        except Exception as e:
            logger.warning("[RecycleBin] 同步 L3 缓存失败 item_id=%s err=%s", item_id, e)

    # 若为 builtin 恢复，从已卸载列表移除
    try:
        if UNINSTALLED_BUILTIN_PATH.exists():
            current = json.loads(UNINSTALLED_BUILTIN_PATH.read_text(encoding="utf-8"))
            if isinstance(current, list) and item_id in current:
                current = [x for x in current if x != item_id]
                UNINSTALLED_BUILTIN_PATH.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.debug("[RecycleBin] 从卸载列表移除失败 item_id=%s err=%s", item_id, e)

    # 恢复 JD 配置文件到 config/hr_jds（若有）
    jd_src = item_dir / "jd_config.md"
    if jd_src.exists():
        _JD_DIR.mkdir(parents=True, exist_ok=True)
        jd_dest = _JD_DIR / f"{item_id}.md"
        try:
            jd_dest.write_text(jd_src.read_text(encoding="utf-8"), encoding="utf-8")
            logger.info("[RecycleBin] 已恢复 JD 文件 item_id=%s", item_id)
        except Exception as e:
            logger.debug("[RecycleBin] 恢复 JD 文件失败 err=%s", e)

    # 恢复 skill_registry 配置（JD_template、prompt_style 等）
    registry_src = item_dir / "registry_config.json"
    if registry_src.exists():
        try:
            cfg = json.loads(registry_src.read_text(encoding="utf-8"))
            if cfg and isinstance(cfg, dict):
                update_skill_config(item_id, cfg)
                logger.info("[RecycleBin] 已恢复 skill_registry 配置 item_id=%s keys=%s", item_id, list(cfg.keys()))
        except Exception as e:
            logger.warning("[RecycleBin] 恢复 skill_registry 失败 err=%s", e)

    # 删除回收站项
    shutil.rmtree(item_dir, ignore_errors=True)

    logger.info("[RecycleBin] 已恢复 item_id=%s recycle_id=%s", item_id, recycle_id)
    return {"ok": True, "item_id": item_id, "recycle_id": recycle_id}


def permanent_delete_from_recycle_bin(recycle_id: str) -> dict[str, Any]:
    """从回收站彻底删除。返回 { ok, recycle_id, error? }"""
    _ensure_recycle_bin()
    item_dir = RECYCLE_BIN_ROOT / recycle_id
    if not item_dir.exists() or not item_dir.is_dir():
        return {"ok": False, "recycle_id": recycle_id, "error": "回收站项不存在"}

    # 加入永久卸载黑名单，防止 L2 同步从 L1 重新拉取
    item_ids_to_block: list[str] = []
    meta_path = item_dir / "meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            item_id = meta.get("item_id", recycle_id.split("_")[0])
            item_ids_to_block.append(item_id)
            # 从 plugin.json 读取 id，L1 manifest 可能用 plugin id
            plugin_path = item_dir / "skill" / "plugin.json"
            if plugin_path.exists():
                try:
                    plugin_data = json.loads(plugin_path.read_text(encoding="utf-8"))
                    pid = plugin_data.get("id")
                    if pid and pid != item_id:
                        item_ids_to_block.append(pid)
                except Exception:
                    pass
        except Exception as e:
            logger.debug("[RecycleBin] 读取 meta 失败 recycle_id=%s err=%s", recycle_id, e)
    if item_ids_to_block:
        try:
            from core.skill_registry import add_permanently_uninstalled
            add_permanently_uninstalled(item_ids_to_block)
        except Exception as e:
            logger.warning("[RecycleBin] 加入永久卸载黑名单失败 err=%s", e)

    try:
        shutil.rmtree(item_dir)
        logger.info("[RecycleBin] 已彻底删除 recycle_id=%s", recycle_id)
        return {"ok": True, "recycle_id": recycle_id}
    except Exception as e:
        logger.warning("[RecycleBin] 彻底删除失败 recycle_id=%s err=%s", recycle_id, e)
        return {"ok": False, "recycle_id": recycle_id, "error": str(e)}
