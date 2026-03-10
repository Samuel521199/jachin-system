"""
com.jachin.hr-assistant - HR 主编排技能
编排各子技能完成完整 HR 流程：

1. The Retriever 获取简历（或本地 PDF/Word）
2. pdf-to-text / docx-parser 解析
3. resume-extractor 结构化提取
4. resume-memory RAG 检索历史成功画像
5. The Tribunal 多 Agent 辩论筛选
6. 输出 Pass 名单

注意：实际编排可在 Forge 中通过 React Flow 连线完成，本技能提供单一入口简化调用。
"""

import logging
from pathlib import Path
from typing import Dict, Any, Optional, List

try:
    from core.skills.base_skill import BaseSkill
except ImportError:
    BaseSkill = object

logger = logging.getLogger(__name__)


class HrAssistantSkill(BaseSkill):
    """HR 主编排技能"""

    def __init__(self, manifest: Dict[str, Any]):
        if BaseSkill is not object:
            super().__init__(manifest)
        else:
            self.manifest = manifest
            self.skill_id = manifest.get("id", "unknown")

    async def execute(self, capability: str, params: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if capability == "full_pipeline":
            return await self.full_pipeline(params, context)
        if capability == "screen_local_resumes":
            return await self.screen_local_resumes(params, context)
        return {"success": False, "error": f"Unknown capability: {capability}"}

    async def full_pipeline(self, params: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        完整流程：岗位 → 获取简历 → RAG → Tribunal → 通过名单
        依赖：retriever-boss、resume-extractor、resume-memory、tribunal
        编排时通过 PluginManager 调用其他技能
        """
        job_title = params.get("job_title", "")
        job_desc = params.get("job_desc", "")
        department = params.get("department", "")
        max_resumes = params.get("max_resumes", 5)

        if not job_title:
            return {"success": False, "error": "job_title is required"}

        # 雏形：返回流程说明，实际需通过 PluginManager.invoke_skill 调用各子技能
        return {
            "success": True,
            "message": "完整流程需在 Forge 中编排各技能",
            "pipeline": [
                "1. com.jachin.retriever-boss.fetch_resumes_by_job",
                "2. com.jachin.resume-extractor.extract_resume (每份)",
                "3. com.jachin.resume-memory.rag_retrieve_success_profile",
                "4. com.jachin.tribunal.screen_resume_debate (每份)",
                "5. 汇总 Pass 名单",
            ],
            "params": {"job_title": job_title, "job_desc": job_desc, "department": department},
        }

    async def screen_local_resumes(
        self, params: Dict[str, Any], context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        对本地简历文件进行筛选。
        流程：pdf-to-text/docx-parser → extract → RAG → Tribunal
        """
        file_paths = params.get("file_paths", [])
        job_desc = params.get("job_desc", "")
        department = params.get("department", "")

        if not file_paths:
            return {"success": False, "error": "file_paths is required"}

        # 雏形：返回流程说明
        return {
            "success": True,
            "message": "本地简历筛选流程，需在 Forge 编排或通过 PluginManager 调用",
            "steps": [
                "对每个 file_path:",
                "  - 若是 .pdf → com.jachin.pdf-to-text.pdf_to_text",
                "  - 若是 .docx → com.jachin.docx-parser.parse_docx",
                "  - com.jachin.resume-extractor.extract_resume",
                "  - com.jachin.resume-memory.rag_retrieve_success_profile",
                "  - com.jachin.tribunal.screen_resume_debate",
            ],
            "params": {"file_paths": file_paths, "job_desc": job_desc, "department": department},
        }
