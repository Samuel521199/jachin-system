"""
com.jachin.tribunal - The Tribunal (仲裁庭)
多智能体辩论筛选：Agent A (Tech) + Agent B (Culture) → Agent C (Judge)

工作流（图3）：
- Round 1: A、B 各自独立输出 Pass/Reject + 理由
- Round 2: 若一致 → 直接交 C；若分歧 → A、B 互相质询辩论
- Round 3: C 综合辩论记录做最终裁决，输出结构化简报
"""

import json
import logging
from typing import Dict, Any, Optional, List, Tuple

try:
    from core.skills.base_skill import BaseSkill
except ImportError:
    BaseSkill = object

logger = logging.getLogger(__name__)

# Persona 提示词（图3 角色设计）
AGENT_A_SYSTEM = """你是 Agent A (Tech Assessor / 左脑能力者)，极其苛刻的技术专家。
关注：候选人过往项目含金量、技术栈匹配度、工作年限。
输出格式：{"verdict": "Pass"|"Reject", "reason": "..."}
"""

AGENT_B_SYSTEM = """你是 Agent B (Culture Fit / 右脑灵魂者)，温和的 HR BP。
关注：稳定性(跳槽频率)、沟通能力暗示、自我评价逻辑性。
输出格式：{"verdict": "Pass"|"Reject", "reason": "..."}
"""

AGENT_C_SYSTEM = """你是 Agent C (The Judge / 裁决者)，资深招聘总监。
综合 Agent A 和 Agent B 的意见，做出最终裁决。
若存在辩论记录，需综合考虑双方争议点。
输出格式：{"verdict": "Pass"|"Reject", "brief": "结构化简报，含双方争议点", "summary": "..."}
"""


def _parse_verdict(text: str) -> Tuple[str, str]:
    """解析 Agent 输出的 verdict 和 reason"""
    try:
        # 尝试解析 JSON
        o = json.loads(text)
        return o.get("verdict", "Reject"), o.get("reason", text)
    except Exception:
        pass
    if "Pass" in text or "通过" in text:
        return "Pass", text
    return "Reject", text


class TribunalSkill(BaseSkill):
    """The Tribunal - 多智能体辩论筛选"""

    def __init__(self, manifest: Dict[str, Any]):
        if BaseSkill is not object:
            super().__init__(manifest)
        else:
            self.manifest = manifest
            self.skill_id = manifest.get("id", "unknown")

    async def execute(self, capability: str, params: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if capability == "screen_resume_debate":
            return await self.screen_resume_debate(params, context)
        if capability == "batch_screen_resumes":
            return await self.batch_screen_resumes(params, context)
        return {"success": False, "error": f"Unknown capability: {capability}"}

    async def screen_resume_debate(self, params: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """对单份简历进行多 Agent 辩论筛选"""
        resume_text = params.get("resume_text", "")
        resume_struct = params.get("resume_struct", {})
        job_desc = params.get("job_desc", "")
        department = params.get("department", "")
        rag_context = params.get("rag_context", "")

        if not resume_text:
            return {"success": False, "error": "resume_text is required"}

        ctx = f"""
岗位描述：
{job_desc}

部门：{department}

历史成功画像（RAG 检索）：
{rag_context or "（暂无）"}

候选人简历：
{resume_text[:6000]}
"""

        # Round 1: A、B 各自表态
        prompt_a = f"{ctx}\n请评估并输出 JSON: verdict (Pass/Reject), reason"
        prompt_b = f"{ctx}\n请从文化契合度评估，输出 JSON: verdict, reason"

        # 集成时替换为真实 LLM 调用
        try:
            llm_invoke = context.get("llm_invoke") if context else None
        except Exception:
            llm_invoke = None

        if llm_invoke:
            resp_a = await llm_invoke(prompt_a, AGENT_A_SYSTEM)
            resp_b = await llm_invoke(prompt_b, AGENT_B_SYSTEM)
        else:
            resp_a = json.dumps({"verdict": "Reject", "reason": "[雏形] 需接入 LLM"})
            resp_b = json.dumps({"verdict": "Reject", "reason": "[雏形] 需接入 LLM"})

        verdict_a, reason_a = _parse_verdict(resp_a)
        verdict_b, reason_b = _parse_verdict(resp_b)

        debate_rounds = 1
        debate_log = []

        # Round 2: 若分歧则辩论
        if verdict_a != verdict_b:
            debate_rounds = 2
            debate_prompt_a = f"Agent B 认为: {reason_b}\n请你反驳或坚持，输出 JSON: verdict, reason"
            debate_prompt_b = f"Agent A 认为: {reason_a}\n请你反驳或坚持，输出 JSON: verdict, reason"
            if llm_invoke:
                resp_a2 = await llm_invoke(debate_prompt_a, AGENT_A_SYSTEM)
                resp_b2 = await llm_invoke(debate_prompt_b, AGENT_B_SYSTEM)
            else:
                resp_a2 = resp_a
                resp_b2 = resp_b
            verdict_a, reason_a = _parse_verdict(resp_a2)
            verdict_b, reason_b = _parse_verdict(resp_b2)
            debate_log = [{"round": 2, "a": reason_a, "b": reason_b}]

        # Round 3: C 裁决
        judge_ctx = f"""
Agent A (技术): {verdict_a} - {reason_a}
Agent B (文化): {verdict_b} - {reason_b}
{"辩论记录: " + json.dumps(debate_log, ensure_ascii=False) if debate_log else ""}

请做出最终裁决，输出 JSON: verdict, brief, summary
"""
        if llm_invoke:
            judge_resp = await llm_invoke(judge_ctx, AGENT_C_SYSTEM)
        else:
            judge_resp = json.dumps({
                "verdict": "Reject" if verdict_a == verdict_b == "Reject" else "Pass",
                "brief": f"A: {reason_a[:200]} | B: {reason_b[:200]}",
                "summary": "[雏形] 需接入 LLM 生成完整简报",
            })

        try:
            judge_obj = json.loads(judge_resp)
        except Exception:
            judge_obj = {"verdict": "Reject", "brief": judge_resp, "summary": ""}

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

    async def batch_screen_resumes(self, params: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """批量筛选"""
        resumes = params.get("resumes", [])
        job_desc = params.get("job_desc", "")
        department = params.get("department", "")

        results = []
        for r in resumes:
            text = r.get("text", r) if isinstance(r, dict) else str(r)
            struct = r.get("struct", {}) if isinstance(r, dict) else {}
            out = await self.screen_resume_debate({
                "resume_text": text,
                "resume_struct": struct,
                "job_desc": job_desc,
                "department": department,
            }, context)
            results.append(out)

        return {"success": True, "results": results, "count": len(results)}
