"""
小脑粗筛 - 第一漏斗底线过滤（云端）
模型与 Key 由 L3 / core.plugin_llm_identity 统一解析（经济型降级模型），
禁止依赖插件 .env 的 BRAIN_FILTER_MODEL / DASHSCOPE_API_KEY。
"""
import json
import logging
import os

logger = logging.getLogger(__name__)


def brain_filter(online_resume_text: str = "", resume_text: str = "", hr_criteria: str = "") -> dict:
    """
    底线过滤：学历、年限等硬指标。
    Args:
        online_resume_text: 在线简历文本（可能残缺）
        hr_criteria: HR 筛选标准摘要
    Returns:
        {"pass": bool, "reason": str, "score": int}
    """
    text = online_resume_text or resume_text
    try:
        from core.plugin_llm_identity import (
            ensure_plugin_dashscope_key_in_env,
            plugin_brain_filter_model_openai_compat,
        )

        api_key = ensure_plugin_dashscope_key_in_env()
        model = plugin_brain_filter_model_openai_compat()
    except ImportError:
        api_key = (os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("QWEN_API_KEY") or "").strip()
        model = "qwen3.5-flash-2026-02-23"

    if not api_key:
        return _rule_fallback(text, hr_criteria)

    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=api_key,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            timeout=15.0,
        )
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
        logger.warning("brain_filter LLM failed: %s", e)
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
