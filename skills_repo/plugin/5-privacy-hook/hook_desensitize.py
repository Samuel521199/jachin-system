"""
v8.0 Nexus Hook - 简历 PII 脱敏
挂载点: before_llm_think
在发往 LLM 前抹除手机号、邮箱等敏感信息。
"""
import re


async def before_llm_think_hook(context: dict, next_middleware) -> None:
    """
    Koa 风格洋葱中间件：拦截 prompt，正则脱敏。
    必须调用 await next_middleware() 将控制权交给下一流程。
    """
    run_id = context.get("run_id", "unknown")[:8]
    prompt = context.get("prompt", "")

    # 手机号脱敏
    prompt = re.sub(r"1[3-9]\d{9}", "[HIDDEN_PHONE]", prompt)
    # 邮箱脱敏
    prompt = re.sub(
        r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
        "[HIDDEN_EMAIL]",
        prompt,
    )
    # 身份证号脱敏（简易）
    prompt = re.sub(r"\d{17}[\dXx]", "[HIDDEN_ID]", prompt)

    context["prompt"] = prompt
    print(f"[RunID: {run_id}] [Aegis] 简历 PII 数据脱敏完毕")

    await next_middleware()
