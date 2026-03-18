"""
技能 API 降级路由：当 skills.py 因 Ray/PluginManager 依赖失败时使用。
提供 GET /api/v3/skills 列表与 POST /{skill_id}/execute 执行，从 ~/.jachin/inventory/skills/ 读取。
"""

from fastapi import APIRouter, HTTPException
from pathlib import Path
from typing import List, Dict, Any
from pydantic import BaseModel

router = APIRouter(prefix="/api/v3/skills", tags=["skills"])


class SkillExecutionRequest(BaseModel):
    capability_name: str
    input_data: Dict[str, Any] = {}


def _inventory_skill_to_info(inv: dict) -> dict:
    """将 inventory 技能格式转为 SkillInfo 兼容格式"""
    perms = inv.get("permissions", [])
    if isinstance(perms, list):
        perm_items = [
            {"id": p.get("scope", p) if isinstance(p, dict) else str(p),
             "label": p.get("scope", p) if isinstance(p, dict) else str(p)}
            for p in perms
        ]
    else:
        perm_items = []
    params = inv.get("params", [])
    caps = [{"name": p if isinstance(p, str) else p.get("name", ""), "description": ""} for p in params] if params else [{"name": "execute", "description": inv.get("description", "")}]
    return {
        "skill_id": inv.get("id", inv.get("item_id", "")),
        "name": inv.get("name", ""),
        "version": inv.get("version", "1.0.0"),
        "description": inv.get("description"),
        "status": "installed",
        "capabilities": caps,
        "permissions": perm_items,
    }


def _find_inventory_skill(skill_id: str) -> Dict[str, Any] | None:
    """按 skill_id 或 item_id 查找 inventory 技能"""
    try:
        from core.inventory_scanner import registered_local_skills
        inv = registered_local_skills.get(skill_id)
        if inv:
            return inv
        for v in registered_local_skills.values():
            if v.get("item_id") == skill_id or v.get("id") == skill_id:
                return v
    except Exception:
        pass
    return None


@router.get("")
async def list_skills_fallback() -> List[Dict[str, Any]]:
    """仅从 inventory 返回技能列表（PluginManager 不可用时的降级）"""
    try:
        from core.inventory_scanner import registered_local_skills
        return [_inventory_skill_to_info(inv) for inv in registered_local_skills.values()]
    except Exception:
        return []


_HR_SKILL_IDS_FALLBACK = ("jpp:com.jachin.hr.analyzer4", "com.jachin.hr.analyzer4")


@router.post("/{skill_id}/execute")
async def execute_skill_fallback(skill_id: str, request: SkillExecutionRequest) -> Dict[str, Any]:
    """执行 inventory 中的 Wasm 技能（PluginManager 不可用时的降级）"""
    import sys
    print(f"[Skill Execute] [L2 Fallback] 开始 skill_id={skill_id} capability={request.capability_name}", file=sys.stderr, flush=True)
    inv = _find_inventory_skill(skill_id)
    if not inv:
        print(f"[Skill Execute] [L2 Fallback] 未找到 skill_id={skill_id}", file=sys.stderr, flush=True)
        raise HTTPException(status_code=404, detail=f"Skill {skill_id} not found in inventory")
    wasm_path = inv.get("wasm_path")
    if not wasm_path or not Path(wasm_path).exists():
        print(f"[Skill Execute] [L2 Fallback] Wasm 不存在 skill_id={skill_id} path={wasm_path}", file=sys.stderr, flush=True)
        return {"success": False, "result": None, "error": "Wasm file not found"}
    try:
        input_data = dict(request.input_data) if request.input_data else {}
        if skill_id in _HR_SKILL_IDS_FALLBACK:
            if not input_data.get("target_role"):
                input_data["target_role"] = "backend_engineer"
            if not input_data.get("resume_filename") and not input_data.get("target_dir"):
                input_data["target_dir"] = "data/hr_resumes"
        try:
            from l3_node.skills import run_tool
            inp = __import__("json").dumps({**input_data, "capability": request.capability_name}, ensure_ascii=False)
            result = run_tool(skill_id, inp, allowed_skills=None)
            print(f"[Skill Execute] [L2 Fallback] 通过 L3 run_tool 完成 skill_id={skill_id}", file=sys.stderr, flush=True)
            return {"success": True, "result": {"text": result}, "error": None}
        except ImportError:
            pass
        from core.wasm_runner import run_wasm_plugin, WasmExecutionError
        stdin_json = {
            "capability": request.capability_name,
            **input_data,
        }
        _hr_files_val = None
        if skill_id in _HR_SKILL_IDS_FALLBACK and input_data.get("target_dir"):
            tdir = (input_data.get("target_dir") or "data/hr_resumes").replace("\\", "/").lstrip("/")
            proj = Path(__file__).resolve().parent.parent.parent
            resume_dir = (proj / tdir).resolve()
            if not resume_dir.is_dir():
                resume_dir = (Path.cwd() / tdir).resolve()
            if resume_dir.is_dir():
                paths = [
                    str(f.resolve()).replace("\\", "/") for f in sorted(resume_dir.iterdir())
                    if f.is_file() and f.suffix.lower() in (".md", ".txt", ".pdf")
                ]
                if paths:
                    _hr_files_val = "|||".join(paths)
                    print(f"[Skill Execute] [L2 Fallback] HR _hr_files count={len(paths)}", file=sys.stderr, flush=True)
        if skill_id in _HR_SKILL_IDS_FALLBACK:
            config_id = "com.jachin.hr.analyzer4"
            item_id = "hr-analyzer4"
            try:
                from core.skill_registry import get_skill_config
                config = get_skill_config(config_id)
                if config:
                    jd_tpl = config.get("JD_template") or config.get("jd_template")
                    if jd_tpl and isinstance(jd_tpl, str):
                        stdin_json["jd_template"] = jd_tpl
                    for k, v in config.items():
                        if k and not k.startswith("_") and k not in ("JD_template", "jd_template"):
                            stdin_json[k] = v
            except Exception:
                pass
            if not stdin_json.get("jd_template"):
                jd_file = proj / "config" / "hr_jds" / f"{item_id}.md"
                if jd_file.exists():
                    try:
                        jd_local = jd_file.read_text(encoding="utf-8", errors="replace").strip()
                        if jd_local:
                            stdin_json["jd_template"] = jd_local
                            print(f"[Skill Execute] [L2 Fallback] 从 config/hr_jds/{item_id}.md 读取 JD", file=sys.stderr, flush=True)
                    except Exception:
                        pass
        if _hr_files_val:
            stdin_str = _hr_files_val + "\n" + __import__("json").dumps(stdin_json, ensure_ascii=False, separators=(",", ":"))
        else:
            stdin_str = __import__("json").dumps(stdin_json, ensure_ascii=False, separators=(",", ":"))
        result = run_wasm_plugin(wasm_path, stdin_json=stdin_str)
        print(f"[Skill Execute] [L2 Fallback] 完成 skill_id={skill_id} result_len={len(str(result))}", file=sys.stderr, flush=True)
        if result is None:
            return {"success": False, "result": None, "error": "WASM execution returned None"}
        result_str = result if isinstance(result, str) else str(result)
        if skill_id in _HR_SKILL_IDS_FALLBACK and result_str:
            try:
                from core.wasm_runner import get_last_ndjson_lines
                from l3_node.hr_analysis_persist import persist_hr_analysis_result, persist_hr_analysis_batch_item
                from l3_node.skills.loader import _extract_stem_from_hr_report, _get_hr_plugin_config_defaults
                import re as _re
                cfg = {}
                try:
                    from core.skill_registry import get_skill_config
                    config_id = "com.jachin.hr.analyzer4"
                    cfg = {**_get_hr_plugin_config_defaults(f"jpp:{config_id}"), **(get_skill_config(config_id) or {})}
                except Exception:
                    cfg = _get_hr_plugin_config_defaults(f"jpp:com.jachin.hr.analyzer4")
                ndjson_lines = get_last_ndjson_lines()
                count = 0
                if ndjson_lines:
                    for line in ndjson_lines:
                        line = (line or "").strip()
                        if not line:
                            continue
                        try:
                            item = __import__("json").loads(line)
                        except Exception:
                            continue
                        if not isinstance(item, dict) or item.get("status") != "progress":
                            continue
                        report = item.get("report_content")
                        if not report or not isinstance(report, str):
                            continue
                        fn = item.get("filename") or ""
                        stem = (Path(fn).stem.replace("_resume", "").replace("_analysis", "").strip() or Path(fn).stem) if fn else ""
                        if not stem or _re.match(r"^resume_\d+$", stem):
                            stem = _extract_stem_from_hr_report(report) or stem or "unknown"
                        persist_hr_analysis_batch_item(skill_id, report, stem, config=cfg)
                        count += 1
                    if count > 0:
                        result_str = f"⚡ 批量分析完成！本次分析 {count} 份简历，报告已保存至 data/hr_analysis/ 目录。"
                elif len(result_str.strip()) > 20 and not result_str.strip().startswith(("⚠️", "[权限", "[未知", "[Wasm", "[执行")):
                    raw = result_str.strip()
                    parsed = None
                    try:
                        parsed = __import__("json").loads(raw)
                    except Exception:
                        if raw.startswith("["):
                            try:
                                end = raw.rfind("]")
                                if end > 0:
                                    parsed = __import__("json").loads(raw[: end + 1])
                            except Exception:
                                pass
                    if isinstance(parsed, list) and parsed:
                        for item in parsed:
                            if not isinstance(item, dict):
                                continue
                            report = item.get("report")
                            if not report or not isinstance(report, str):
                                continue
                            fn = item.get("filename") or ""
                            stem = (Path(fn).stem.replace("_resume", "").replace("_analysis", "").strip() or Path(fn).stem) if fn else ""
                            if not stem or _re.match(r"^resume_\d+$", stem):
                                stem = _extract_stem_from_hr_report(report) or stem or "unknown"
                            persist_hr_analysis_batch_item(skill_id, report, stem, config=cfg)
                            count += 1
                        if count > 0:
                            result_str = f"⚡ 批量分析完成！本次分析 {count} 份简历，报告已保存至 data/hr_analysis/ 目录。"
                    else:
                        persist_hr_analysis_result(skill_id, result_str, input_data, config=cfg)
            except ImportError:
                print("[Skill Execute] [L2 Fallback] l3_node 不可用，跳过 HR 报告持久化", file=sys.stderr, flush=True)
            except Exception as pe:
                print(f"[Skill Execute] [L2 Fallback] HR 持久化失败: {pe}", file=sys.stderr, flush=True)
        if isinstance(result, str):
            import json
            if not result_str.strip():
                return {"success": True, "result": {"text": "[执行完成但无输出，请检查 Wasm 插件或 execute ABI 返回值]"}, "error": None}
            try:
                out = json.loads(result_str)
                return {"success": True, "result": out, "error": None}
            except json.JSONDecodeError:
                return {"success": True, "result": {"text": result_str}, "error": None}
        return {"success": True, "result": result if isinstance(result, dict) else {"text": result_str, "value": result}, "error": None}
    except WasmExecutionError as e:
        print(f"[Skill Execute] [L2 Fallback] WASM 异常 skill_id={skill_id} error={e} wasm_details={getattr(e, 'wasm_details', '')[:200]}", file=sys.stderr, flush=True)
        return {"success": False, "result": None, "error": str(e), "wasm_details": getattr(e, "wasm_details", "")}
    except Exception as e:
        print(f"[Skill Execute] [L2 Fallback] 异常 skill_id={skill_id} error={e}", file=sys.stderr, flush=True)
        return {"success": False, "result": None, "error": str(e)}
