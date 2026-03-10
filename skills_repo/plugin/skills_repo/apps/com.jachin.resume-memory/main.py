"""
com.jachin.resume-memory - 简历核心记忆与 RAG
将优秀员工简历标记 is_core=True 存入 LanceDB，支持 RAG 检索历史成功画像
"""

import json
import logging
import os
from pathlib import Path
from typing import Dict, Any, Optional, List

try:
    from core.skills.base_skill import BaseSkill
except ImportError:
    BaseSkill = object

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = os.environ.get("JACHIN_WORKSPACE", os.path.expanduser("~/.jachin/workspace"))
RESUME_TABLE = "resume_core_memory"


def _get_db_path() -> Path:
    p = Path(DEFAULT_DB_PATH) / "lancedb" / "resume_memory"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _get_embedding(text: str) -> List[float]:
    """占位 embedding；可替换为 sentence-transformers 或 API"""
    try:
        import hashlib
        h = hashlib.sha256(text.encode()).hexdigest()
        return [int(h[i : i + 2], 16) / 255.0 - 0.5 for i in range(0, 32, 2)]
    except Exception:
        return [0.0] * 16


class ResumeMemorySkill(BaseSkill):
    """简历核心记忆与 RAG 检索技能"""

    def __init__(self, manifest: Dict[str, Any]):
        if BaseSkill is not object:
            super().__init__(manifest)
        else:
            self.manifest = manifest
            self.skill_id = manifest.get("id", "unknown")

    async def execute(self, capability: str, params: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if BaseSkill is not object:
            return await super().execute(capability, params, context)
        if capability == "store_core_resume":
            return await self.store_core_resume(params)
        if capability == "rag_retrieve_success_profile":
            return await self.rag_retrieve_success_profile(params)
        return {"success": False, "error": f"Unknown capability: {capability}"}

    async def store_core_resume(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """将优秀员工简历存入 LanceDB 核心记忆"""
        try:
            resume_text = params.get("resume_text", "")
            resume_struct = params.get("resume_struct", {})
            department = params.get("department", "")

            if not resume_text:
                return {"success": False, "error": "resume_text is required"}

            embedding = _get_embedding(resume_text)
            db_path = _get_db_path()

            try:
                import lancedb
                db = lancedb.connect(str(db_path))
                if RESUME_TABLE not in db.table_names():
                    db.create_table(
                        RESUME_TABLE,
                        [{
                            "vector": embedding,
                            "text": resume_text[:20000],
                            "struct": json.dumps(resume_struct, ensure_ascii=False),
                            "department": department,
                            "is_core": True,
                        }],
                    )
                else:
                    tbl = db.open_table(RESUME_TABLE)
                    tbl.add([{
                        "vector": embedding,
                        "text": resume_text[:20000],
                        "struct": json.dumps(resume_struct, ensure_ascii=False),
                        "department": department,
                        "is_core": True,
                    }])
                return {"success": True, "message": "Core resume stored", "department": department}
            except ImportError as e:
                return {"success": False, "error": f"lancedb not installed: {e}"}
        except Exception as e:
            logger.error(f"store_core_resume failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def rag_retrieve_success_profile(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """RAG 检索：根据部门历史成功画像评估候选人"""
        try:
            candidate_text = params.get("candidate_text", "")
            department = params.get("department", "")
            top_k = params.get("top_k", 5)

            if not candidate_text:
                return {"success": False, "error": "candidate_text is required"}

            query_vector = _get_embedding(candidate_text)
            db_path = _get_db_path()

            try:
                import lancedb
                db = lancedb.connect(str(db_path))
                if RESUME_TABLE not in db.table_names():
                    return {
                        "success": True,
                        "profiles": [],
                        "message": "No core memory yet, add 优秀员工简历 first",
                        "query_embedding_used": True,
                    }

                tbl = db.open_table(RESUME_TABLE)
                results = tbl.search(query_vector).limit(top_k).to_list()
                profiles = []
                for r in results:
                    if department and r.get("department") and department != r.get("department"):
                        continue
                    profiles.append({
                        "text_preview": (r.get("text") or "")[:500],
                        "department": r.get("department", ""),
                        "struct": r.get("struct"),
                    })
                    if len(profiles) >= top_k:
                        break

                return {
                    "success": True,
                    "profiles": profiles,
                    "query_embedding_used": True,
                    "prompt_suggestion": "根据当前部门的历史成功画像，评估此人。",
                }
            except ImportError as e:
                return {"success": False, "error": f"lancedb not installed: {e}"}
        except Exception as e:
            logger.error(f"rag_retrieve_success_profile failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
