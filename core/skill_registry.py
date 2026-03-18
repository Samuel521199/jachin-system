"""
Jachin 注册表 (K-V 配置) + 动态数据卷 (VFS) 管理

- skill_registry: 技能级 K-V 配置（如 JD_template 等动态提示词）
- volume_bindings: 技能与数据卷的绑定，引用计数用于 GC
- ~/.jachin/volumes/: 所有共享/私有数据卷的物理根目录
"""
from __future__ import annotations

import logging
import secrets
import shutil
from pathlib import Path
from typing import Any

from core.db import get_connection

logger = logging.getLogger(__name__)

VOLUMES_ROOT = Path.home() / ".jachin" / "volumes"
UNINSTALLED_BUILTIN_PATH = Path.home() / ".jachin" / "uninstalled_builtin_skills.json"
PERMANENTLY_UNINSTALLED_PATH = Path.home() / ".jachin" / "permanently_uninstalled_skills.json"


def ensure_volumes_root() -> None:
    """确保 volumes 根目录存在"""
    VOLUMES_ROOT.mkdir(parents=True, exist_ok=True)


def _skill_id_to_item_id(skill_id: str) -> str:
    """skill_id (jpp:xxx) -> item_id (目录名)，用于与 inventory 一致"""
    if skill_id.startswith("jpp:"):
        return skill_id[4:]
    return skill_id


def _get_plugin_config_defaults(skill_id: str) -> dict[str, Any]:
    """从 plugin.json 读取 configs 默认值（内置/缓存技能未入 registry 时兜底）"""
    raw = _skill_id_to_item_id(skill_id)
    # 映射到可能的目录名
    item_ids = [raw]
    if "com.jachin.hr.analyzer4" in raw or raw == "com.jachin.hr.analyzer4":
        item_ids.append("hr-analyzer4")
    proj_root = Path(__file__).resolve().parent.parent
    search_dirs = [
        proj_root / "l3_node" / "skills" / "wasm_plugins",
        Path.home() / ".jachin" / "inventory" / "skills",
        Path.home() / ".jachin" / "l3_skill_cache",
    ]
    for base in search_dirs:
        for item_id in item_ids:
            plugin_path = base / item_id / "plugin.json"
            if plugin_path.exists():
                try:
                    import json
                    data = json.loads(plugin_path.read_text(encoding="utf-8"))
                    configs = data.get("configs")
                    if isinstance(configs, dict):
                        return {k: v for k, v in configs.items() if not (k and str(k).startswith("_"))}
                except Exception as e:
                    logger.debug("[SkillRegistry] 读取 plugin.json 失败 path=%s err=%s", plugin_path, e)
    return {}


def setup_skill_registry_and_volumes(
    skill_id: str,
    item_id: str,
    configs: dict[str, Any] | None = None,
    volumes: list[dict[str, Any]] | None = None,
) -> None:
    """
    技能安装时：解析 plugin.json 的 configs 和 volumes，
    写入 skill_registry 默认值，创建数据卷目录，写入 volume_bindings。
    """
    ensure_volumes_root()
    conn = get_connection()
    try:
        # configs: { "JD_template": "默认值", "prompt_style": "strict" }
        if configs and isinstance(configs, dict):
            for key, val in configs.items():
                if key.startswith("_"):
                    continue
                value_str = str(val) if val is not None else ""
                type_str = "string"
                if isinstance(val, bool):
                    type_str = "bool"
                elif isinstance(val, (int, float)):
                    type_str = "number"
                elif isinstance(val, (list, dict)):
                    import json
                    value_str = json.dumps(val, ensure_ascii=False)
                    type_str = "json"
                rid = f"sr-{secrets.token_hex(6)}"
                conn.execute(
                    """
                    INSERT OR IGNORE INTO skill_registry (id, skill_id, key, value, type, updated_at)
                    VALUES (?, ?, ?, ?, ?, strftime('%s', 'now'))
                    """,
                    (rid, item_id, key, value_str, type_str),
                )
            conn.commit()

        # volumes: [ { "name": "hr_data", "access_mode": "rw" } ]
        if volumes and isinstance(volumes, list):
            for vol in volumes:
                if not isinstance(vol, dict):
                    continue
                vol_name = vol.get("name") or vol.get("volume_name")
                if not vol_name or not isinstance(vol_name, str):
                    continue
                access_mode = vol.get("access_mode", "rw")
                if access_mode not in ("rw", "ro"):
                    access_mode = "rw"
                vol_path = VOLUMES_ROOT / vol_name
                vol_path.mkdir(parents=True, exist_ok=True)
                vid = f"vb-{secrets.token_hex(6)}"
                conn.execute(
                    """
                    INSERT OR IGNORE INTO volume_bindings (id, volume_name, skill_id, access_mode)
                    VALUES (?, ?, ?, ?)
                    """,
                    (vid, vol_name, item_id, access_mode),
                )
            conn.commit()
    finally:
        conn.close()


def _resolve_registry_key(skill_id: str) -> str:
    """解析 skill_id 为 registry 存储用的 key（与 get 查询一致）"""
    raw = _skill_id_to_item_id(skill_id)
    if "com.jachin.hr.analyzer4" in raw or raw == "com.jachin.hr.analyzer4":
        return "hr-analyzer4"
    return raw


def _sync_jd_template_to_file(skill_id: str, jd_content: str) -> None:
    """将 JD_template 同步写入 config/skills/.../hr_jds/ 下对应文件（规范 075）"""
    if not jd_content or not isinstance(jd_content, str):
        return
    item_id = _resolve_registry_key(skill_id)
    if item_id != "hr-analyzer4":
        return
    proj_root = Path(__file__).resolve().parent.parent
    from l3_node.jachin_config import get_hr_jds_dir
    jd_dir = get_hr_jds_dir(proj_root)
    jd_dir.mkdir(parents=True, exist_ok=True)
    jd_file = jd_dir / f"{item_id}.md"
    try:
        jd_file.write_text(jd_content.strip(), encoding="utf-8")
        logger.info("[SkillRegistry] JD 已同步至文件 path=%s", jd_file)
    except Exception as e:
        logger.warning("[SkillRegistry] JD 同步文件失败 path=%s err=%s", jd_file, e)


def update_skill_config(skill_id: str, config_data: dict[str, Any]) -> dict[str, int]:
    """
    更新技能在 skill_registry 中的键值对。
    返回 {"updated": N, "inserted": M}。
    HR 技能：JD_template 会同步写入 config/skills/.../hr_jds/{item_id}.md
    """
    item_id = _resolve_registry_key(skill_id)
    conn = get_connection()
    updated = 0
    inserted = 0
    try:
        for key, val in config_data.items():
            if not key or key.startswith("_"):
                continue
            value_str = str(val) if val is not None else ""
            type_str = "string"
            if isinstance(val, bool):
                type_str = "bool"
                value_str = "true" if val else "false"
            elif isinstance(val, (int, float)):
                type_str = "number"
            elif isinstance(val, (list, dict)):
                import json
                value_str = json.dumps(val, ensure_ascii=False)
                type_str = "json"
            row = conn.execute(
                "SELECT id FROM skill_registry WHERE skill_id = ? AND key = ?",
                (item_id, key),
            ).fetchone()
            if row:
                conn.execute(
                    """
                    UPDATE skill_registry SET value = ?, type = ?, updated_at = strftime('%s', 'now')
                    WHERE skill_id = ? AND key = ?
                    """,
                    (value_str, type_str, item_id, key),
                )
                updated += 1
            else:
                rid = f"sr-{secrets.token_hex(6)}"
                conn.execute(
                    """
                    INSERT INTO skill_registry (id, skill_id, key, value, type, updated_at)
                    VALUES (?, ?, ?, ?, ?, strftime('%s', 'now'))
                    """,
                    (rid, item_id, key, value_str, type_str),
                )
                inserted += 1
        conn.commit()
        # HR 技能：JD_template 同步到 config/skills/.../hr_jds/{item_id}.md
        jd_val = config_data.get("JD_template") or config_data.get("jd_template")
        if jd_val and isinstance(jd_val, str):
            _sync_jd_template_to_file(skill_id, jd_val)
        return {"updated": updated, "inserted": inserted}
    finally:
        conn.close()


def get_skill_config(skill_id: str) -> dict[str, Any]:
    """获取技能在 skill_registry 中的键值对。skill_id 可为 item_id 或 jpp:xxx"""
    conn = get_connection()
    # 尝试多种 key：与 update 一致的 registry key、原始 item_id
    candidates = [_resolve_registry_key(skill_id), _skill_id_to_item_id(skill_id)]
    try:
        rows = []
        for c in candidates:
            rows = conn.execute(
                "SELECT key, value, type FROM skill_registry WHERE skill_id = ?",
                (c,),
            ).fetchall()
            if rows:
                break
        result: dict[str, Any] = {}
        for row in rows:
            key, value, type_str = row[0], row[1], row[2]
            if type_str == "bool":
                result[key] = value.lower() in ("true", "1", "yes")
            elif type_str == "number":
                try:
                    result[key] = float(value) if "." in value else int(value)
                except ValueError:
                    result[key] = value
            elif type_str == "json":
                import json
                try:
                    result[key] = json.loads(value)
                except json.JSONDecodeError:
                    result[key] = value
            else:
                result[key] = value
        # 内置/缓存技能未入 registry 时，优先从 config/skills/.../hr_jds/{item_id}.md 读取 JD
        if not result:
            result = _get_plugin_config_defaults(skill_id)
            item_id = _resolve_registry_key(skill_id)
            if item_id == "hr-analyzer4":
                from l3_node.jachin_config import get_hr_jds_dir
                proj_root = Path(__file__).resolve().parent.parent
                jd_file = get_hr_jds_dir(proj_root) / f"{item_id}.md"
                if jd_file.exists():
                    try:
                        result["JD_template"] = jd_file.read_text(encoding="utf-8").strip()
                    except Exception as e:
                        logger.debug("[SkillRegistry] 读取 JD 文件失败 path=%s err=%s", jd_file, e)
                # 路径项兜底（plugin 未找到或未声明时）
                if "resume_input_dir" not in result:
                    result["resume_input_dir"] = "data/hr_resumes"
                if "output_dir" not in result:
                    result["output_dir"] = "data/hr_analysis"
        else:
            # registry 有部分记录时，合并 plugin.json 默认值，确保新增的 config 项（如 resume_input_dir、output_dir）也能展示
            defaults = _get_plugin_config_defaults(skill_id)
            for k, v in defaults.items():
                if k not in result:
                    result[k] = v
            # HR 技能兜底：若 plugin 未找到或缺少路径项，显式补全
            item_id = _resolve_registry_key(skill_id)
            if item_id == "hr-analyzer4":
                if "resume_input_dir" not in result:
                    result["resume_input_dir"] = "data/hr_resumes"
                if "output_dir" not in result:
                    result["output_dir"] = "data/hr_analysis"
        return result
    finally:
        conn.close()


def cleanup_builtin_skill_artifacts(item_id: str) -> dict[str, Any]:
    """
    清理内置技能的残留：JD 文件、skill_registry、加入已卸载列表。
    供 L2 在技能不在 inventory 时调用（如用户卸载 wasm_plugins 中的技能）。
    """
    result: dict[str, Any] = {"ok": True, "item_id": item_id, "jd_deleted": False, "registry_cleaned": False}
    proj_root = Path(__file__).resolve().parent.parent
<<<<<<< HEAD
    # 1. 删除 config/skills/.../hr_jds/ 下相关 JD 文件（兼容 hr-analyzer2 与 hr_analyzer2 命名）
    from l3_node.jachin_config import get_hr_jds_dir
    jd_dir = get_hr_jds_dir(proj_root)
=======
    # 1. 删除 config/hr_jds/ 下相关 JD 文件（兼容 hr-analyzer4 与 hr_analyzer4 命名）
    jd_dir = proj_root / "config" / "hr_jds"
>>>>>>> v0.8.35
    for name in (f"{item_id}.md", f"{item_id.replace('-', '_')}.md"):
        jd_file = jd_dir / name
        if jd_file.exists():
            try:
                jd_file.unlink()
                result["jd_deleted"] = True
                logger.info("[SkillRegistry] 已删除 JD 文件 item_id=%s path=%s", item_id, jd_file)
            except Exception as e:
                logger.warning("[SkillRegistry] 删除 JD 文件失败 path=%s err=%s", jd_file, e)
    # 2. 清理 skill_registry（兼容多种 key）
    conn = get_connection()
    try:
        plugin_ids = [item_id, f"jpp:{item_id}"]
        if item_id == "hr-analyzer4":
            plugin_ids.extend(["com.jachin.hr.analyzer4", "jpp:com.jachin.hr.analyzer4"])
        for sid in plugin_ids:
            cur = conn.execute("DELETE FROM skill_registry WHERE skill_id = ?", (sid,))
            if cur.rowcount > 0:
                result["registry_cleaned"] = True
        conn.commit()
    finally:
        conn.close()
    # 3. 加入已卸载列表（L3 将过滤这些技能）
    try:
        UNINSTALLED_BUILTIN_PATH.parent.mkdir(parents=True, exist_ok=True)
        current: list[str] = []
        if UNINSTALLED_BUILTIN_PATH.exists():
            import json
            raw = UNINSTALLED_BUILTIN_PATH.read_text(encoding="utf-8")
            current = json.loads(raw) if raw.strip() else []
        if item_id not in current:
            current.append(item_id)
            import json
            UNINSTALLED_BUILTIN_PATH.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info("[SkillRegistry] 已加入卸载列表 item_id=%s", item_id)
    except Exception as e:
        logger.warning("[SkillRegistry] 写入卸载列表失败 err=%s", e)
    return result


def get_uninstalled_builtin_skills() -> set[str]:
    """读取用户已卸载的内置技能列表（供 L3 过滤）"""
    if not UNINSTALLED_BUILTIN_PATH.exists():
        return set()
    try:
        import json
        raw = UNINSTALLED_BUILTIN_PATH.read_text(encoding="utf-8")
        lst = json.loads(raw) if raw.strip() else []
        return set(lst) if isinstance(lst, list) else set()
    except Exception:
        return set()


def add_permanently_uninstalled(item_ids: list[str]) -> None:
    """将技能加入永久卸载黑名单（回收站彻底删除后调用，防止 L2 同步重新拉取）"""
    if not item_ids:
        return
    current: set[str] = set()
    if PERMANENTLY_UNINSTALLED_PATH.exists():
        try:
            import json
            raw = PERMANENTLY_UNINSTALLED_PATH.read_text(encoding="utf-8")
            lst = json.loads(raw) if raw.strip() else []
            current = set(lst) if isinstance(lst, list) else set()
        except Exception:
            pass
    current.update(str(x).strip() for x in item_ids if x and str(x).strip())
    try:
        PERMANENTLY_UNINSTALLED_PATH.parent.mkdir(parents=True, exist_ok=True)
        import json
        PERMANENTLY_UNINSTALLED_PATH.write_text(
            json.dumps(sorted(current), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info("[SkillRegistry] 已加入永久卸载黑名单 item_ids=%s", list(current))
    except Exception as e:
        logger.warning("[SkillRegistry] 写入永久卸载列表失败 err=%s", e)


def get_permanently_uninstalled_skills() -> set[str]:
    """读取永久卸载黑名单（供 L2 同步、L3 加载过滤）"""
    if not PERMANENTLY_UNINSTALLED_PATH.exists():
        return set()
    try:
        import json
        raw = PERMANENTLY_UNINSTALLED_PATH.read_text(encoding="utf-8")
        lst = json.loads(raw) if raw.strip() else []
        return set(lst) if isinstance(lst, list) else set()
    except Exception:
        return set()


def uninstall_skill_with_gc(item_id: str, purge_data: bool) -> dict[str, Any]:
    """
    卸载技能并执行 GC：
    1. 删除 ~/.jachin/inventory/skills/{item_id}
    2. purge_data=true 时删除 skill_registry 记录
    3. 删除 volume_bindings 记录，引用计数归零且 purge_data 时删除物理目录
    """
    from core.inventory_scanner import SKILLS_DIR

    result: dict[str, Any] = {
        "ok": True,
        "item_id": item_id,
        "inventory_removed": False,
        "registry_cleaned": False,
        "volumes_gced": [],
    }

    # 1. 删除 L2 静态资产
    inv_path = SKILLS_DIR / item_id
    if inv_path.exists() and inv_path.is_dir():
        try:
            shutil.rmtree(inv_path)
            result["inventory_removed"] = True
            logger.info("[SkillRegistry] 已删除 inventory 目录 item_id=%s", item_id)
        except Exception as e:
            logger.warning("[SkillRegistry] 删除 inventory 失败 item_id=%s err=%s", item_id, e)
            result["ok"] = False
            result["error"] = str(e)
            return result

    conn = get_connection()
    try:
        # 2. 清理注册表（卸载时始终清除配置，与 purge_data 无关）
        cur = conn.execute("DELETE FROM skill_registry WHERE skill_id = ?", (item_id,))
        if cur.rowcount > 0:
            result["registry_cleaned"] = True
        conn.commit()

        # 3. 数据卷引用计数
        rows = conn.execute(
            "SELECT id, volume_name FROM volume_bindings WHERE skill_id = ?",
            (item_id,),
        ).fetchall()
        for row in rows:
            vb_id, vol_name = row[0], row[1]
            conn.execute("DELETE FROM volume_bindings WHERE id = ?", (vb_id,))
            conn.commit()
            # 检查该 volume 是否还有其它技能引用
            remaining = conn.execute(
                "SELECT 1 FROM volume_bindings WHERE volume_name = ? LIMIT 1",
                (vol_name,),
            ).fetchone()
            if not remaining and purge_data:
                vol_path = VOLUMES_ROOT / vol_name
                if vol_path.exists() and vol_path.is_dir():
                    try:
                        shutil.rmtree(vol_path)
                        result["volumes_gced"].append(vol_name)
                        logger.info("[SkillRegistry] 数据卷引用归零已删除 volume=%s", vol_name)
                    except Exception as e:
                        logger.warning("[SkillRegistry] 删除数据卷失败 volume=%s err=%s", vol_name, e)
    finally:
        conn.close()

    return result
