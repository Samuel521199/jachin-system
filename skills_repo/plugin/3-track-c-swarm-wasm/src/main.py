"""
轨道 C - 虫群评审引擎 (HR Swarm Engine)
三专家多 Agent 交叉评分：Tech Lead + HR BP → 主理法官裁决
- 专家 A (Tech Lead): qwen3.5-122b-a10b，1220 亿参数，逻辑纵深
- 专家 B (HR BP): qwen3.5-plus，长文本语义、情商均衡
- 专家 C (Judge): qwen3.5-397b-a17b，近 4000 亿参数，指令遵循
"""
from __future__ import annotations

import json
import re
import sys
import asyncio
from pathlib import Path

# 项目根 (plugin/) 加入 path，以便导入 src.llm_client
_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_root))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from jachin_sdk import jachin_plugin


def _call_llm(prompt: str, system: str, model: str | None = None) -> str:
    """同步调用 LLM（兼容已有事件循环），支持按模型分配"""
    async def _do_invoke():
        try:
            from src.llm_client import invoke_llm_with_model, invoke_llm
            if model:
                return await invoke_llm_with_model(prompt, system, model)
            return await invoke_llm(prompt, system)
        except ImportError:
            return _invoke_llm_fallback(prompt, system, model)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is None:
        return asyncio.run(_do_invoke())
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor() as pool:
        return pool.submit(asyncio.run, _do_invoke()).result()


def _invoke_llm_fallback(prompt: str, system: str, model: str | None = None) -> str:
    """回退：DashScope 或 Gemini"""
    from dotenv import load_dotenv
    import os
    load_dotenv(_root / ".env")
    # 优先 DashScope
    key = os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("ALIBABA_API_KEY")
    if key:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=key, base_url="https://dashscope.aliyuncs.com/compatible-mode/v1")
            m = model or os.environ.get("HR_BP_MODEL", "qwen-plus")
            resp = client.chat.completions.create(
                model=m,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
            )
            if resp.choices and resp.choices[0].message.content:
                return resp.choices[0].message.content.strip()
        except Exception:
            pass
    # Gemini 回退
    gkey = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if gkey:
        try:
            from google import genai
            from google.genai import types
            client = genai.Client(api_key=gkey)
            resp = asyncio.run(client.aio.models.generate_content(
                model=os.environ.get("HR_PLUGIN_LLM_MODEL", "gemini-2.0-flash"),
                contents=prompt,
                config=types.GenerateContentConfig(system_instruction=system, temperature=0.3),
            ))
            if resp and getattr(resp, "text", None):
                return resp.text.strip()
        except Exception:
            pass
    return json.dumps({"verdict": "Pass", "reason": "[规则模式] 需配置 DASHSCOPE_API_KEY 或 GEMINI_API_KEY", "score": 60})


def _parse_expert_output(text: str) -> tuple[str, str, int]:
    """解析专家输出：verdict, reason, score"""
    try:
        o = json.loads(text)
        v = o.get("verdict", "Reject")
        r = o.get("reason", text)
        s = int(o.get("score", 60))
        return (v, r, s)
    except Exception:
        pass
    if "Pass" in text or "通过" in text:
        return ("Pass", text, 70)
    return ("Reject", text, 50)


def _extract_json(text: str) -> dict | None:
    """从文本中提取包含 decision 的 JSON 对象"""
    # 尝试直接解析
    try:
        o = json.loads(text.strip())
        if isinstance(o, dict) and "decision" in o:
            return o
    except Exception:
        pass
    # 提取 ```json ... ``` 代码块
    m = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    # 查找第一个 { 到匹配的 }
    start = text.find("{")
    if start >= 0:
        depth, i = 0, start
        for i, c in enumerate(text[start:], start):
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        o = json.loads(text[start : i + 1])
                        if isinstance(o, dict) and "decision" in o:
                            return o
                    except Exception:
                        pass
                    break
    return None


@jachin_plugin
def hr_swarm_engine(resume_text: str, hr_criteria: str) -> dict:
    """
    三专家多 Agent 交叉评审：Tech Lead + HR BP → 主理法官裁决
    """
    if not resume_text:
        return {"decision": "淘汰", "tech_score": 0, "hr_score": 0, "error": "resume_text 为空"}

    from src.llm_client import TECH_LEAD_MODEL, HR_BP_MODEL, JUDGE_MODEL

    ctx = f"""
HR 筛选标准：
{hr_criteria or "（未提供）"}

候选人简历：
{resume_text[:6000]}
"""

    # Round 1: 专家 A - 技术总监 (qwen-coder)
    tech_prompt = ctx + "\n作为苛刻的技术总监，评估技术栈、项目含金量、年限。严格输出 JSON: {\"verdict\": \"Pass\"|\"Reject\", \"reason\": \"...\", \"score\": 0-100}"
    tech_resp = _call_llm(tech_prompt, "你是资深技术总监，极其严格，能精准揪出技术名词的无脑堆砌。", TECH_LEAD_MODEL)
    verdict_t, reason_t, tech_score = _parse_expert_output(tech_resp)

    # Round 2: 专家 B - HR BP (qwen-plus)
    hr_prompt = ctx + "\n作为 HR BP，评估稳定性、跳槽频率、求职动机、自我评价与经历是否矛盾。严格输出 JSON: {\"verdict\": \"Pass\"|\"Reject\", \"reason\": \"...\", \"score\": 0-100}"
    hr_resp = _call_llm(hr_prompt, "你是资深 HR BP，关注稳定性与文化契合，长文本理解出色。", HR_BP_MODEL)
    verdict_h, reason_h, hr_score = _parse_expert_output(hr_resp)

    # Round 3: 专家 C - 主理法官 (qwen-max)
    judge_input = f"""
技术总监评分：{tech_score} 分
技术总监理由：{reason_t}

HR BP 评分：{hr_score} 分
HR BP 理由：{reason_h}

请综合两者，做出最终裁决。严格按以下 JSON 格式输出，不要包含其他文字：
{{"decision": "建议面试"|"淘汰", "tech_score": {tech_score}, "hr_score": {hr_score}, "tech_reason": "...", "hr_reason": "...", "brief": "一句话摘要"}}
"""
    judge_resp = _call_llm(judge_input, "你是主理法官，指令遵循能力极强。综合技术总监与 HR BP 的评分，输出纯净的 JSON，不要有任何多余文字。", JUDGE_MODEL)
    judge_obj = _extract_json(judge_resp)

    if judge_obj:
        decision = judge_obj.get("decision", "建议面试" if tech_score >= 60 and hr_score >= 60 else "淘汰")
        tech_reason = judge_obj.get("tech_reason", reason_t)
        hr_reason = judge_obj.get("hr_reason", reason_h)
        brief = judge_obj.get("brief", f"技术{tech_score}分 / 稳定性{hr_score}分 → {decision}")
    else:
        # 法官解析失败时，代码级兜底
        decision = "建议面试" if tech_score >= 60 and hr_score >= 60 else "淘汰"
        tech_reason, hr_reason, brief = reason_t, reason_h, f"技术{tech_score}分 / 稳定性{hr_score}分 → {decision}"

    return {
        "decision": decision,
        "tech_score": tech_score,
        "hr_score": hr_score,
        "tech_reason": tech_reason,
        "hr_reason": hr_reason,
        "brief": brief,
    }


if __name__ == "__main__":
    from jachin_sdk import run
    run()
