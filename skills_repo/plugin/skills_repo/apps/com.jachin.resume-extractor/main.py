"""
com.jachin.resume-extractor - 简历结构化提取
从简历文本中提取姓名、学历、工作经历、技能等结构化信息
基于规则 + 简单模式匹配，可后续接入 LLM 做更深层次提取
"""

import re
import logging
from typing import Dict, Any, Optional, List

try:
    from core.skills.base_skill import BaseSkill
except ImportError:
    BaseSkill = object

logger = logging.getLogger(__name__)

EDUCATION_KEYWORDS = ["学历", "教育", "本科", "硕士", "博士", "大学", "学院", "专业", "毕业"]
EXPERIENCE_KEYWORDS = ["工作经历", "工作经验", "实习", "项目经历", "项目经验"]
SKILL_KEYWORDS = ["技能", "技术", "熟悉", "掌握", "精通", "了解", "擅长"]
CONTACT_PATTERNS = {
    "email": re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"),
    "phone": re.compile(r"1[3-9]\d{9}|0\d{2,3}-?\d{7,8}|(?:\d{3,4}[- ]?){2}\d{4}"),
}


class ResumeExtractorSkill(BaseSkill):
    """简历结构化提取技能"""

    def __init__(self, manifest: Dict[str, Any]):
        if BaseSkill is not object:
            super().__init__(manifest)
        else:
            self.manifest = manifest
            self.skill_id = manifest.get("id", "unknown")

    async def execute(self, capability: str, params: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if BaseSkill is not object:
            return await super().execute(capability, params, context)
        if capability == "extract_resume":
            return await self.extract_resume(params)
        return {"success": False, "error": f"Unknown capability: {capability}"}

    async def extract_resume(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """从文本中提取简历结构化字段"""
        try:
            text = params.get("text", "")
            if not text:
                return {"success": False, "error": "text is required"}

            result: Dict[str, Any] = {
                "name": "",
                "email": "",
                "phone": "",
                "education": [],
                "work_experience": [],
                "skills": [],
                "summary": "",
                "raw_sections": {},
            }

            emails = CONTACT_PATTERNS["email"].findall(text)
            if emails:
                result["email"] = emails[0]
            phones = CONTACT_PATTERNS["phone"].findall(text)
            if phones:
                result["phone"] = phones[0]

            lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
            for line in lines[:5]:
                if re.match(r"^[\u4e00-\u9fa5]{2,4}$", line) and "简历" not in line and "个人" not in line:
                    result["name"] = line
                    break

            sections = self._split_sections(text)
            result["raw_sections"] = sections

            for kw in ["教育", "学历", "学习经历"]:
                if kw in sections:
                    result["education"] = self._parse_list_items(sections[kw])
                    break

            for kw in ["工作经历", "工作经验", "项目经历", "项目经验"]:
                if kw in sections:
                    result["work_experience"] = self._parse_list_items(sections[kw])
                    break

            for kw in ["技能", "技术栈", "专业技能"]:
                if kw in sections:
                    result["skills"] = self._parse_skills(sections[kw])
                    break

            for kw in ["自我评价", "个人简介", "摘要"]:
                if kw in sections:
                    result["summary"] = sections[kw].strip()[:500]
                    break

            return {"success": True, "result": result}
        except Exception as e:
            logger.error(f"extract_resume failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    def _split_sections(self, text: str) -> Dict[str, str]:
        sections: Dict[str, str] = {}
        current_key = "intro"
        current_content: List[str] = []
        for line in text.split("\n"):
            matched = False
            for kw in EDUCATION_KEYWORDS + EXPERIENCE_KEYWORDS + SKILL_KEYWORDS + ["自我评价", "个人简介"]:
                if line.strip().startswith(kw) or line.strip().rstrip("：:") == kw:
                    if current_content:
                        sections[current_key] = "\n".join(current_content).strip()
                    current_key = kw
                    current_content = []
                    matched = True
                    break
            if not matched and line.strip():
                current_content.append(line)
        if current_content:
            sections[current_key] = "\n".join(current_content).strip()
        return sections

    def _parse_list_items(self, text: str) -> List[str]:
        items = []
        for line in text.split("\n"):
            line = line.strip()
            line = re.sub(r"^[\d一二三四五六七八九十]+[.、．\s)\]]+", "", line)
            line = re.sub(r"^[●○◆◇▪▫★☆\-\*]\s*", "", line)
            if len(line) > 5:
                items.append(line)
        return items[:20]

    def _parse_skills(self, text: str) -> List[str]:
        raw = re.sub(r"[\s]+", " ", text)
        skills = re.split(r"[,，、;；\n]", raw)
        return [s.strip() for s in skills if len(s.strip()) >= 2][:30]
