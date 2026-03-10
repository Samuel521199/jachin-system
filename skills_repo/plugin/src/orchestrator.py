"""
HR 招聘插件 - 主编排器
完整流程：搜索简历 → 筛选（多 Agent 辩论）→ 输出通过名单 → （可选）面试流程同步
"""
import asyncio
import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional

from .skills.retriever import fetch_resumes_by_job, check_cookie_status
from .skills.pdf_parser import pdf_to_text
from .skills.docx_parser import parse_docx
from .skills.resume_extractor import extract_resume
from .skills.tribunal import batch_screen_resumes
from .skills.lark_hr import sync_interview_track

logger = logging.getLogger(__name__)

# 演示用模拟简历（无 Boss Cookie 或本地文件时使用）
DEMO_RESUMES = [
    {
        "text": """
张三
电话：13800138000 | 邮箱：zhangsan@example.com

教育背景：
- 某大学 计算机科学 本科 2018-2022

工作经历：
- 某科技公司 Python 开发工程师 2022-2024
  负责后端 API 开发，使用 Django/FastAPI
- 某创业公司 实习 2021
  参与数据平台搭建

技能：
Python, Django, FastAPI, PostgreSQL, Redis

自我评价：
热爱技术，沟通能力强，有责任心。
""",
        "struct": {},
    },
    {
        "text": """
李四
手机：13900139000 | Email: lisi@test.com

学历：硕士 某985 软件工程 2020-2023

项目经历：
- 分布式系统设计与实现
- 微服务架构迁移

技术栈：Java, Spring Cloud, Kubernetes, MySQL

个人总结：注重代码质量，有团队协作经验。
""",
        "struct": {},
    },
]


async def run_full_pipeline(
    job_title: str = "",
    job_desc: str = "",
    department: str = "",
    resume_source: str = "demo",  # demo | boss | local
    resume_files: Optional[List[str]] = None,
    max_resumes: int = 10,
    sync_to_lark: bool = False,
    lark_sheet_token: str = "",
) -> Dict[str, Any]:
    """
    运行完整 HR 招聘流程：
    1. 获取简历（demo/boss/local）
    2. 解析 + 提取（若为文件）
    3. 多 Agent 辩论筛选
    4. 输出通过名单
    5. （可选）同步到 Lark 面试流程
    """
    resumes: List[Dict[str, Any]] = []
    parse_errors: List[str] = []

    # Step 1: 获取简历
    if resume_source == "boss":
        out = await fetch_resumes_by_job(job_title, job_desc, max_resumes)
        if not out.get("success"):
            return {
                "success": False,
                "error": out.get("error", "Boss 搜索失败"),
                "stage": "fetch",
                "resumes": [],
                "passed": [],
            }
        for r in out.get("resumes", []):
            raw = r.get("raw", "")
            ext_out = await extract_resume(raw)
            struct = ext_out.get("result", {}) if ext_out.get("success") else {}
            resumes.append({"text": raw, "struct": struct})
    elif resume_source == "local" and resume_files:
        for fp in resume_files[:max_resumes]:
            path = Path(fp).resolve()
            if not path.exists():
                parse_errors.append(f"文件不存在: {fp}")
                continue
            ext = path.suffix.lower()
            if ext == ".pdf":
                out = await pdf_to_text(str(path))
            elif ext in (".docx", ".doc"):
                out = await parse_docx(str(path))
            else:
                parse_errors.append(f"不支持格式: {ext}")
                continue
            if not out.get("success"):
                parse_errors.append(out.get("error", "解析失败"))
                continue
            text = out.get("text", "")
            ext_out = await extract_resume(text)
            struct = ext_out.get("result", {}) if ext_out.get("success") else {}
            resumes.append({"text": text, "struct": struct, "source_file": str(path)})
    else:
        # demo：使用模拟简历
        resumes = DEMO_RESUMES[:max_resumes].copy()

    if not resumes:
        return {
            "success": False,
            "error": "无可用简历" + ("；" + "; ".join(parse_errors) if parse_errors else ""),
            "stage": "fetch",
            "resumes": [],
            "passed": [],
        }

    # Step 2 & 3: 多 Agent 辩论筛选
    screen_out = await batch_screen_resumes(
        resumes=resumes,
        job_desc=job_desc or "（未提供岗位描述）",
        department=department,
    )
    passed = screen_out.get("passed", [])
    results = screen_out.get("results", [])

    # Step 4: 输出通过名单
    output = {
        "success": True,
        "stage": "complete",
        "summary": {
            "total": len(resumes),
            "passed": len(passed),
            "rejected": len(results) - len(passed),
        },
        "resumes": results,
        "passed": passed,
        "passed_briefs": [
            {
                "verdict": p.get("verdict"),
                "brief": p.get("brief", ""),
                "summary": p.get("summary", ""),
                "resume_preview": p.get("resume_preview", "")[:200],
                "agent_a": p.get("agent_a_opinion", ""),
                "agent_b": p.get("agent_b_opinion", ""),
            }
            for p in passed
        ],
        "parse_errors": parse_errors,
    }

    # Step 5: （可选）同步到 Lark
    if sync_to_lark and lark_sheet_token:
        lark_out = await sync_interview_track(lark_sheet_token, passed)
        output["lark_sync"] = lark_out

    return output


def run_sync(**kwargs) -> Dict[str, Any]:
    """同步包装，供非 async 环境调用"""
    return asyncio.run(run_full_pipeline(**kwargs))
