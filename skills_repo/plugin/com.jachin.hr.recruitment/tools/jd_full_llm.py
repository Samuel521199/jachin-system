"""
无发帖场景：jd.json 中 jd_full 为空时，用 LLM 根据岗位名与 jd.json 已有字段生成正文并落盘。
供透析 / DAG 分析前调用；已存在非空 jd_full 时跳过。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _jd_full_is_empty(doc: dict[str, Any]) -> bool:
    return not (str(doc.get("jd_full") or "").strip())


def _strip_code_fences(text: str) -> str:
    t = (text or "").strip()
    t = re.sub(r"^```\w*\n?", "", t)
    t = re.sub(r"\n?```\s*$", "", t)
    return t.strip()


def _jd_llm_disabled() -> bool:
    v = (os.environ.get("HR_JD_FULL_LLM") or "").strip().lower()
    return v in ("0", "false", "no", "off", "disable", "disabled")


def _resolve_llm_engine() -> Any:
    try:
        from l3_node.agent_ref import engine_ref

        eng = engine_ref.get("engine")
        if eng is not None:
            return eng
    except Exception:
        pass
    try:
        from l3_node.__main__ import _create_engine_standalone

        return _create_engine_standalone()
    except Exception as e:
        logger.warning("[JD-LLM] 无法创建 LLM 引擎: %s", e)
        return None


def _build_jd_prompt_user(
    job_name: str,
    jd: dict[str, Any],
    extra_context: str,
) -> str:
    title = (jd.get("job_title") or job_name or "").strip() or job_name
    lines = [
        "请根据以下信息，撰写一份**中文**招聘职位说明正文（用于简历筛选与透析匹配），直接输出正文，不要用 Markdown 代码块包裹。",
        "",
        f"岗位名称：{title}",
    ]
    loc = (jd.get("job_location") or jd.get("city") or "").strip()
    if loc:
        lines.append(f"工作地点：{loc}")
    edu = (jd.get("education") or "").strip()
    if edu:
        lines.append(f"学历要求：{edu}")
    exp = (jd.get("experience") or "").strip()
    if exp:
        lines.append(f"经验要求：{exp}")
    smin, smax = jd.get("salary_min"), jd.get("salary_max")
    if smin is not None and smax is not None:
        try:
            lines.append(f"薪资范围：{int(smin)}-{int(smax)}K（或按当地习惯表述）")
        except (TypeError, ValueError):
            lines.append(f"薪资：{smin}-{smax}")
    fk = (jd.get("focus_keywords") or "").strip()
    if fk:
        lines.append(f"关注关键词/技能：{fk}")
    if extra_context.strip():
        lines.extend(["", "HR / 业务补充说明（可融入 JD）：", extra_context.strip()])
    lines.extend(
        [
            "",
            "结构建议（用纯文本标题行即可）：岗位职责、任职要求、加分项；内容要具体可评估，避免空泛口号。",
            "篇幅约 400～1200 字。",
        ]
    )
    return "\n".join(lines)


async def _generate_jd_full_async(
    job_name: str,
    jd: dict[str, Any],
    extra_context: str,
) -> str:
    engine = _resolve_llm_engine()
    if not engine:
        return ""
    sys_prompt = (
        "你是资深招聘顾问。只输出职位说明正文（中文），不要开场白、不要重复用户给出的字段列表标题，"
        "不要使用 ``` 代码围栏。"
    )
    user_prompt = _build_jd_prompt_user(job_name, jd, extra_context)
    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_prompt},
    ]
    result = await engine.generate_response(
        messages,
        temperature=0.35,
        max_tokens=2800,
        l3_call_purpose="hr_jd_full",
    )
    if isinstance(result, dict):
        text = (result.get("content") or "").strip()
    else:
        text = (result or "").strip()
    return _strip_code_fences(text)


def _run_coro_sync(coro: Any, *, timeout: float) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(asyncio.run, coro)
        return fut.result(timeout=timeout)


def ensure_jd_full_via_llm_sync(
    jd_config_path: str,
    job_name: str,
    *,
    extra_context: str = "",
) -> dict[str, Any]:
    """
    若 jd.json 存在且 jd_full 为空，则调用 LLM 生成并写回 jd.json（保留其它键）。

    Returns:
        {"ok": bool, "written": bool, "skipped": str|None, "error": str|None}
    """
    if _jd_llm_disabled():
        return {"ok": True, "written": False, "skipped": "HR_JD_FULL_LLM disabled", "error": None}

    p = Path(jd_config_path)
    if not p.is_file():
        return {"ok": False, "written": False, "skipped": None, "error": "jd_config_path not found"}

    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        return {"ok": False, "written": False, "skipped": None, "error": f"read jd.json: {e}"}

    if not isinstance(raw, dict):
        raw = {}

    if not _jd_full_is_empty(raw):
        return {"ok": True, "written": False, "skipped": "jd_full already set", "error": None}

    timeout = float(os.environ.get("HR_JD_FULL_LLM_TIMEOUT", "180") or "180")

    try:
        text = _run_coro_sync(
            _generate_jd_full_async(job_name, raw, extra_context),
            timeout=timeout,
        )
    except Exception as e:
        logger.warning("[JD-LLM] 生成失败: %s", e)
        return {"ok": False, "written": False, "skipped": None, "error": str(e)}

    text = (text or "").strip()
    if len(text) < 80:
        return {"ok": False, "written": False, "skipped": None, "error": "llm returned too short"}

    raw["jd_full"] = text
    raw["jd_full_llm_generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
    try:
        p.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        return {"ok": False, "written": False, "skipped": None, "error": f"write jd.json: {e}"}

    logger.info("[JD-LLM] 已写入 jd_full len=%d path=%s", len(text), p)
    return {"ok": True, "written": True, "skipped": None, "error": None}
