"""简历结构化提取"""
import re
import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

CONTACT_PATTERNS = {
    "email": re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"),
    "phone": re.compile(r"1[3-9]\d{9}|0\d{2,3}-?\d{7,8}|(?:\d{3,4}[- ]?){2}\d{4}"),
}
SECTION_KW = ["教育", "学历", "工作经历", "工作经验", "项目经历", "技能", "自我评价", "个人简介"]


def _split_sections(text: str) -> Dict[str, str]:
    sections: Dict[str, str] = {}
    current_key, current_content = "intro", []
    for line in text.split("\n"):
        matched = False
        for kw in SECTION_KW:
            if line.strip().startswith(kw) or line.strip().rstrip("：:") == kw:
                if current_content:
                    sections[current_key] = "\n".join(current_content).strip()
                current_key, current_content = kw, []
                matched = True
                break
        if not matched and line.strip():
            current_content.append(line)
    if current_content:
        sections[current_key] = "\n".join(current_content).strip()
    return sections


def _parse_list_items(text: str) -> List[str]:
    items = []
    for line in text.split("\n"):
        line = re.sub(r"^[\d一二三四五六七八九十]+[.、．\s)\]]+", "", line.strip())
        line = re.sub(r"^[●○◆◇▪▫★☆\-\*]\s*", "", line)
        if len(line) > 5:
            items.append(line)
    return items[:20]


def _parse_skills(text: str) -> List[str]:
    raw = re.sub(r"[\s]+", " ", text)
    return [s.strip() for s in re.split(r"[,，、;；\n]", raw) if len(s.strip()) >= 2][:30]


async def extract_resume(text: str) -> Dict[str, Any]:
    """从文本提取简历结构化字段"""
    try:
        if not text:
            return {"success": False, "error": "text 为空", "result": {}}

        result: Dict[str, Any] = {
            "name": "", "email": "", "phone": "",
            "education": [], "work_experience": [], "skills": [], "summary": "",
            "raw_sections": {},
        }

        for m in CONTACT_PATTERNS["email"].findall(text):
            result["email"] = m
            break
        for m in CONTACT_PATTERNS["phone"].findall(text):
            result["phone"] = m
            break

        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        for line in lines[:5]:
            if re.match(r"^[\u4e00-\u9fa5]{2,4}$", line) and "简历" not in line and "个人" not in line:
                result["name"] = line
                break

        sections = _split_sections(text)
        result["raw_sections"] = sections
        for kw in ["教育", "学历", "学习经历"]:
            if kw in sections:
                result["education"] = _parse_list_items(sections[kw])
                break
        for kw in ["工作经历", "工作经验", "项目经历", "项目经验"]:
            if kw in sections:
                result["work_experience"] = _parse_list_items(sections[kw])
                break
        for kw in ["技能", "技术栈", "专业技能"]:
            if kw in sections:
                result["skills"] = _parse_skills(sections[kw])
                break
        for kw in ["自我评价", "个人简介", "摘要"]:
            if kw in sections:
                result["summary"] = sections[kw].strip()[:500]
                break

        return {"success": True, "result": result}
    except Exception as e:
        logger.error(f"extract_resume failed: {e}", exc_info=True)
        return {"success": False, "error": str(e), "result": {}}
