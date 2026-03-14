"""The Tribunal - 多 Agent 辩论筛选（搜索后 → 筛选 → 输出通过名单）"""
import json
import logging
from typing import Dict, Any, Optional, List, Tuple

logger = logging.getLogger(__name__)

AGENT_A_SYSTEM = """你是 Agent A (Tech Assessor / 左脑能力者)，极其苛刻的技术专家。
关注：候选人过往项目含金量、技术栈匹配度、工作年限。
输出 JSON: {"verdict": "Pass"|"Reject", "reason": "..."}"""

AGENT_B_SYSTEM = """你是 Agent B (Culture Fit / 右脑灵魂者)，温和的 HR BP。
关注：稳定性(跳槽频率)、沟通能力暗示、自我评价逻辑性。
输出 JSON: {"verdict": "Pass"|"Reject", "reason": "..."}"""

AGENT_C_SYSTEM = """你是 Agent C (The Judge / 裁决者)，资深招聘总监。
综合 Agent A 和 Agent B 的意见，做出最终裁决。
输出 JSON: {"verdict": "Pass"|"Reject", "brief": "结构化简报", "summary": "..."}"""


def _parse_verdict(text: str) -> Tuple[str, str]:
    try:
        o = json.loads(text)
        return o.get("verdict", "Reject"), o.get("reason", text)
    except Exception:
        pass
    if "Pass" in text or "通过" in text:
        return "Pass", text
    return "Reject", text


async def _call_llm(prompt: str, system: str) -> str:
    from ..llm_client import invoke_llm
    return await invoke_llm(prompt, system)


async def screen_resume_debate(
    resume_text: str,
    job_desc: str = "",
    department: str = "",
    rag_context: str = "",
    resume_struct: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    对单份简历进行多 Agent 辩论筛选。
    返回 verdict(Pass/Reject)、brief、agent_a_opinion、agent_b_opinion 等。
    """
    if not resume_text:
        return {"success": False, "error": "resume_text 为空", "verdict": "Reject"}

    ctx = f"""
岗位描述：
{job_desc}

部门：{department}

历史成功画像（RAG）：
{rag_context or "（暂无）"}

候选人简历：
{resume_text[:6000]}
"""

    # Round 1
    resp_a = await _call_llm(ctx + "\n请评估并输出 JSON: verdict, reason", AGENT_A_SYSTEM)
    resp_b = await _call_llm(ctx + "\n请从文化契合度评估，输出 JSON: verdict, reason", AGENT_B_SYSTEM)
    verdict_a, reason_a = _parse_verdict(resp_a)
    verdict_b, reason_b = _parse_verdict(resp_b)

    debate_rounds, debate_log = 1, []

    # Round 2: 分歧则辩论
    if verdict_a != verdict_b:
        debate_rounds = 2
        resp_a2 = await _call_llm(f"Agent B 认为: {reason_b}\n请你反驳或坚持，输出 JSON: verdict, reason", AGENT_A_SYSTEM)
        resp_b2 = await _call_llm(f"Agent A 认为: {reason_a}\n请你反驳或坚持，输出 JSON: verdict, reason", AGENT_B_SYSTEM)
        verdict_a, reason_a = _parse_verdict(resp_a2)
        verdict_b, reason_b = _parse_verdict(resp_b2)
        debate_log = [{"round": 2, "a": reason_a, "b": reason_b}]

    # Round 3: C 裁决
    judge_ctx = f"""
Agent A: {verdict_a} - {reason_a}
Agent B: {verdict_b} - {reason_b}
{"辩论: " + json.dumps(debate_log, ensure_ascii=False) if debate_log else ""}

请做出最终裁决，输出 JSON: verdict, brief, summary
"""
    judge_resp = await _call_llm(judge_ctx, AGENT_C_SYSTEM)
    try:
        judge_obj = json.loads(judge_resp)
    except Exception:
        judge_obj = {"verdict": "Pass" if verdict_a == verdict_b == "Pass" else "Reject", "brief": judge_resp, "summary": ""}

    return {
        "success": True,
        "verdict": judge_obj.get("verdict", "Reject"),
        "brief": judge_obj.get("brief", ""),
        "summary": judge_obj.get("summary", ""),
        "agent_a_opinion": reason_a,
        "agent_b_opinion": reason_b,
        "debate_rounds": debate_rounds,
        "debate_log": debate_log,
    }


async def batch_screen_resumes(
    resumes: List[Dict[str, Any]],
    job_desc: str = "",
    department: str = "",
) -> Dict[str, Any]:
    """批量筛选，返回每份的 verdict 及通过名单"""
    from .resume_memory import rag_retrieve_success_profile

    results = []
    for r in resumes:
        text = r.get("text", r.get("raw", "")) if isinstance(r, dict) else str(r)
        struct = r.get("struct", {}) if isinstance(r, dict) else {}
        if not text:
            continue

        # RAG 检索
        rag_out = await rag_retrieve_success_profile(text, department, top_k=3)
        rag_context = "\n".join([p.get("text_preview", "") for p in rag_out.get("profiles", [])])

        out = await screen_resume_debate(
            resume_text=text,
            job_desc=job_desc,
            department=department,
            rag_context=rag_context,
            resume_struct=struct,
        )
        out["resume_preview"] = text[:300]
        out["struct"] = struct
        results.append(out)

    passed = [x for x in results if x.get("verdict") == "Pass"]
    return {
        "success": True,
        "results": results,
        "count": len(results),
        "passed": passed,
        "passed_count": len(passed),
        "rejected_count": len(results) - len(passed),
    }
