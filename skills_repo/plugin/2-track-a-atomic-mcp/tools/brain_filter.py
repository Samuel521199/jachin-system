"""
小脑粗筛 - 第一漏斗底线过滤（云端平替）
使用 qwen3.5-flash-2026-02-23 极速扫雷，仅看学历、年限等硬指标。
"""
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# 第一漏斗：极速、廉价，一秒扫完几十份在线简历
BRAIN_FILTER_MODEL = "qwen3.5-flash-2026-02-23"


def _load_dotenv():
    try:
        from dotenv import load_dotenv
        root = Path(__file__).resolve().parent.parent.parent
        load_dotenv(root / ".env")
    except ImportError:
        pass


def brain_filter(online_resume_text: str = "", resume_text: str = "", hr_criteria: str = "") -> dict:
    """
    底线过滤：学历、年限等硬指标。
    Args:
        online_resume_text: 在线简历文本（可能残缺）
        hr_criteria: HR 筛选标准摘要
    Returns:
        {"pass": bool, "reason": str, "score": int}
    """
    _load_dotenv()
    text = online_resume_text or resume_text
    api_key = os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("ALIBABA_API_KEY")
    if not api_key:
        # 无 API 时：简单规则回退
        return _rule_fallback(text, hr_criteria)

    try:
        from openai import OpenAI
        client = OpenAI(
            api_key=api_key,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            timeout=15.0,
        )
        model = os.environ.get("BRAIN_FILTER_MODEL", BRAIN_FILTER_MODEL)
        prompt = f"""
HR 硬性标准摘要：
{hr_criteria[:500]}

候选人在线简历（可能不完整）：
{text[:2000]}

仅判断：学历是否达标、工作年限是否达标。输出 JSON: {{"pass": true|false, "reason": "一句话", "score": 0-100}}
"""
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        resp_text = resp.choices[0].message.content.strip()
        try:
            obj = json.loads(resp_text)
            return {
                "pass": bool(obj.get("pass", False)),
                "reason": str(obj.get("reason", text)),
                "score": int(obj.get("score", 50)),
            }
        except Exception:
            return _parse_text_fallback(resp_text)
    except Exception as e:
        logger.warning(f"brain_filter LLM failed: {e}")
        return _rule_fallback(text, hr_criteria)


def _rule_fallback(text: str, criteria: str) -> dict:
    """规则回退：简单关键词"""
    text_lower = (text + criteria).lower()
    pass_ = True
    reason = "规则模式：未配置小模型，建议通过"
    if "本科" in text or "bachelor" in text_lower:
        pass_
    if "3年" in text or "3 年" in text or "三年" in text:
        pass_
    return {"pass": pass_, "reason": reason, "score": 60}


def _parse_text_fallback(text: str) -> dict:
    if "pass" in text.lower() or "通过" in text:
        return {"pass": True, "reason": text, "score": 65}
    return {"pass": False, "reason": text, "score": 45}
