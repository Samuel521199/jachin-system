"""
HR 透析镜 MCP 工具：根据岗位要求分析简历，输出 Markdown 报告。

包装 Wasm 技能 com.jachin.hr.analyzer4，供 Agent 以 MCP 工具形式调用。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

HR_SKILL_ID = "jpp:com.jachin.hr.analyzer4"


def hr_analyze_resume(
    target_dir: str,
    jd_template: str,
    target_role: str = "backend_engineer",
    focus_keywords: str = "",
    strictness: str = "standard",
    output_dir: str = "",
) -> str:
    """
    根据岗位 JD 分析指定目录下的简历，输出 Markdown 评估报告。

    Args:
        target_dir: 简历所在目录（如 data/hr_resumes 或 ~/.jachin/workspace/hr_recruitment/岗位名/pending）
        jd_template: 岗位 JD 全文
        target_role: 目标角色（如 backend_engineer）
        focus_keywords: 重点关注关键词，逗号分隔
        strictness: 严格程度 standard|strict|relaxed
        output_dir: 输出目录，空则使用技能配置默认值

    Returns:
        分析报告文本，失败时返回错误信息
    """
    try:
        from l3_node.skills import run_tool
    except ImportError as e:
        logger.warning("[hr_analyze_resume] 无法导入 run_tool: %s", e)
        return f"错误：L3 技能加载器不可用，{e}"

    target_dir = (target_dir or "").strip() or "data/hr_resumes"
    jd_template = (jd_template or "").strip()
    if not jd_template:
        return "错误：jd_template 不能为空，请传入岗位 JD"

    # 解析 target_dir 下的简历文件
    try:
        base = Path(target_dir)
        if not base.is_absolute():
            # 相对路径：~/.jachin/workspace 或项目根
            try:
                from .config import get_data_root, get_resume_root
                if "hr_resumes" in target_dir or target_dir.strip() in ("data/hr_resumes", "hr_resumes"):
                    base = get_resume_root()
                elif "/" in target_dir or "\\" in target_dir:
                    base = get_data_root() / target_dir.replace("\\", "/").strip("/")
                else:
                    base = get_data_root() / target_dir
            except ImportError:
                from l3_node.paths import get_app_root
                base = get_app_root() / target_dir
        if not base.exists() or not base.is_dir():
            return f"错误：简历目录不存在 {base}"
        files = list(base.glob("*.md")) + list(base.glob("*.pdf")) + list(base.glob("*.docx"))
        paths_str = "|||".join(str(f.relative_to(base)) for f in files[:50])
        if not paths_str:
            return f"错误：目录 {base} 下无简历文件（支持 .md/.pdf/.docx）"
    except Exception as e:
        logger.warning("[hr_analyze_resume] 解析目录失败: %s", e)
        return f"错误：解析简历目录失败，{e}"

    input_data: dict[str, Any] = {
        "target_dir": str(base),
        "_hr_files": paths_str,
        "jd_template": jd_template,
        "strictness": (strictness or "standard").strip(),
        "target_role": (target_role or "backend_engineer").strip(),
    }
    if focus_keywords and str(focus_keywords).strip():
        input_data["focus_keywords"] = str(focus_keywords).strip()
    if output_dir and str(output_dir).strip():
        input_data["output_dir"] = str(output_dir).strip()

    inp = json.dumps({**input_data, "capability": "execute"}, ensure_ascii=False)
    try:
        result = run_tool(HR_SKILL_ID, inp, allowed_skills=None)
        return result or "分析完成，无输出"
    except Exception as e:
        logger.exception("[hr_analyze_resume] 执行失败")
        return f"错误：透析镜执行失败，{e}"
