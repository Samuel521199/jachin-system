"""简历核心记忆与 RAG"""
import json
import logging
import os
from pathlib import Path
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

DB_PATH = Path(os.environ.get("HR_PLUGIN_WORKSPACE", os.path.expanduser("~/.hr_plugin/workspace"))) / "lancedb" / "resume_memory"
RESUME_TABLE = "resume_core_memory"


def _get_embedding(text: str) -> List[float]:
    try:
        import hashlib
        h = hashlib.sha256(text.encode()).hexdigest()
        return [int(h[i : i + 2], 16) / 255.0 - 0.5 for i in range(0, 32, 2)]
    except Exception:
        return [0.0] * 16


async def store_core_resume(
    resume_text: str,
    resume_struct: Dict[str, Any] = None,
    department: str = "",
) -> Dict[str, Any]:
    """将优秀员工简历存入 LanceDB"""
    try:
        if not resume_text:
            return {"success": False, "error": "resume_text 为空"}
        DB_PATH.mkdir(parents=True, exist_ok=True)
        embedding = _get_embedding(resume_text)
        import lancedb
        db = lancedb.connect(str(DB_PATH))
        row = {
            "vector": embedding,
            "text": resume_text[:20000],
            "struct": json.dumps(resume_struct or {}, ensure_ascii=False),
            "department": department,
            "is_core": True,
        }
        if RESUME_TABLE not in db.table_names():
            db.create_table(RESUME_TABLE, [row])
        else:
            db.open_table(RESUME_TABLE).add([row])
        return {"success": True, "message": "已存入核心记忆", "department": department}
    except ImportError as e:
        return {"success": False, "error": f"lancedb 未安装: {e}"}
    except Exception as e:
        logger.error(f"store_core_resume failed: {e}", exc_info=True)
        return {"success": False, "error": str(e)}


async def rag_retrieve_success_profile(
    candidate_text: str,
    department: str = "",
    top_k: int = 5,
) -> Dict[str, Any]:
    """RAG 检索历史成功画像"""
    try:
        if not candidate_text:
            return {"success": False, "error": "candidate_text 为空", "profiles": []}
        DB_PATH.mkdir(parents=True, exist_ok=True)
        import lancedb
        db = lancedb.connect(str(DB_PATH))
        if RESUME_TABLE not in db.table_names():
            return {"success": True, "profiles": [], "message": "暂无核心记忆"}
        tbl = db.open_table(RESUME_TABLE)
        q = _get_embedding(candidate_text)
        results = tbl.search(q).limit(top_k).to_list()
        profiles = []
        for r in results:
            if department and r.get("department") and department != r.get("department"):
                continue
            profiles.append({"text_preview": (r.get("text") or "")[:500], "department": r.get("department", "")})
            if len(profiles) >= top_k:
                break
        return {"success": True, "profiles": profiles}
    except ImportError as e:
        return {"success": False, "error": f"lancedb 未安装: {e}", "profiles": []}
    except Exception as e:
        logger.error(f"rag_retrieve failed: {e}", exc_info=True)
        return {"success": False, "error": str(e), "profiles": []}
