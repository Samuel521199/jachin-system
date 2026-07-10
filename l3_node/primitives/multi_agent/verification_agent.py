"""
Verification Agent — 对抗性验证 SubAgent 角色（SSOT）。

设计原则（借鉴 Claude Code verification 类型）：
- 任务是**证明交付物不能工作**，而不是走过场盖章
- 必须基于**实际核对**（读文件 / 跑命令 / 阅读内联数据），禁止未验证即 PASS
- User-facing result **必须**含 ``VERDICT: PASS | FAIL | PARTIAL`` 行

用法：
- delegate: ``{"role": "verification", "task": "..."}``
- inline dict: ``build_verification_role(allowed_tools=[...])``
- PMO 阶段二: ``PMO_VERIFICATION_ROLE`` + ``PMO_ENABLE_VERIFICATION_AUDIT=1``
"""
from __future__ import annotations

import re
from typing import Any

VERIFICATION_ROLE_ID = "verification"

# 带执行能力：读文件 + 跑测试/构建/curl 等
VERIFICATION_TOOLS_WITH_EXEC: list[str] = [
    "core:fs_read",
    "core:shell_exec",
    "core:shell_job_status",
    "core:shell_job_cancel",
]

# 纯文本/JSON 审计：无工具（PMO 交叉审计等）
VERIFICATION_TOOLS_READONLY: list[str] = []

VERIFICATION_SYSTEM_PROMPT = (
    "你是 Verification Agent（对抗性验证专员）。\n"
    "你的职责**不是**帮实现者找理由通过，而是**尽力证明交付物有问题**——"
    "找错误、不一致、未覆盖的边界、数据异常、未执行的假设。\n\n"
    "【工作方式】\n"
    "1. **先核对再下结论**：能读文件就读、能跑测试/构建/curl 就跑；"
    "禁止在未实际核对的情况下给出 PASS。\n"
    "2. **对抗性思维**：假设实现是错的，列出你尝试过的「搞砸路径」及结果。\n"
    "3. **证据链**：每条发现须附具体依据（文件路径+行号、命令输出摘要、JSON 字段名、数据片段）。\n"
    "4. **不确定即标注**：无法判定的项单独列出，不得假装已验证。\n\n"
    "【输出格式 · 强制】\n"
    "User-facing result 须含以下结构（Markdown）：\n"
    "## 验证摘要\n"
    "（1–3 句：验证了什么、怎么验证的）\n"
    "## 发现\n"
    "- 🔴 阻断项：…（无则写「无」）\n"
    "- 🟡 风险/不确定：…（无则写「无」）\n"
    "- 🟢 已核对且通过项：…\n"
    "## VERDICT\n"
    "VERDICT: PASS | FAIL | PARTIAL\n\n"
    "VERDICT 含义：\n"
    "- **PASS**：已实际核对，未发现阻断项\n"
    "- **FAIL**：存在阻断项，交付物不可用或明显错误\n"
    "- **PARTIAL**：有进展但存在未解决风险/不确定项，或部分检查因数据/环境不足无法完成\n\n"
    "⛔ 禁止：未跑测试/未读文件/未核对数据就输出 VERDICT: PASS。"
)

VERIFICATION_VERDICT_RE = re.compile(
    r"VERDICT\s*:\s*(PASS|FAIL|PARTIAL)\b",
    re.IGNORECASE,
)


def parse_verification_verdict(text: str) -> str:
    """从 Verification Agent 输出解析 VERDICT；未找到则返回 UNKNOWN。"""
    if not (text or "").strip():
        return "UNKNOWN"
    m = VERIFICATION_VERDICT_RE.search(text)
    if not m:
        return "UNKNOWN"
    return m.group(1).upper()


def build_verification_role(
    *,
    allowed_tools: list[str] | None = None,
    system_prefix_extra: str = "",
) -> dict[str, Any]:
    """构造 inline verification 角色 dict（供 fanout / _run_sub_agent）。"""
    tools = (
        list(allowed_tools)
        if allowed_tools is not None
        else list(VERIFICATION_TOOLS_WITH_EXEC)
    )
    prefix = VERIFICATION_SYSTEM_PROMPT
    if system_prefix_extra.strip():
        prefix = prefix + "\n\n" + system_prefix_extra.strip()
    return {
        "id": VERIFICATION_ROLE_ID,
        "name": "Verification Agent",
        "system_prefix": prefix,
        "allowed_tools": tools,
    }


def format_verification_run_report_line(verdict: str, preview: str = "") -> str:
    """供 delegate / fanout Verification evidence 首行摘要。"""
    line = f"[verification RunReport] VERDICT={verdict}"
    if preview.strip():
        line += f" | {preview.strip()[:120]}"
    return line


# PMO 阶段二：无工具，仅基于内联 JSON 做交叉审计
PMO_VERIFICATION_EXTRA = (
    "【PMO 交叉审计 · 专向约束】\n"
    "⛔ **你无任何可用工具**——禁止 db_query / fs_read / shell_exec / 任何 MCP。\n"
    "阶段一 Worker A/B/C 的全部 JSON **已在 user 消息中内联**，请直接阅读文本。\n"
    "须检查：\n"
    "1. **大需求层级**：Worker C 的 epics[] 是否仅为顶层大需求；epic_children[] 是否通过 parent_epic 正确挂接\n"
    "2. **幽灵需求**：Epic/主表有、人员看板（vewCz1FFJi）无（或反之）——须引用具体 Requirement 名称\n"
    "3. **状态倒挂**：同一需求在不同视图状态矛盾\n"
    "4. **人员超载**：👥 以 Worker B 的 personnel_tasks[] 为准；按计划周期×完成进度×当前时间判定 🚨/🟡/✅\n"
    "5. **Sprint 集合差**：两视图 Sprint 不一致项\n"
    "输出须含《项目风险诊断书》（Markdown ## 章节；每条风险含 ⚠️ 与依据）。\n"
    "数据不足时标注「数据不足·结论仅供参考」，VERDICT 用 PARTIAL 而非 PASS。"
)

PMO_VERIFICATION_ROLE: dict[str, Any] = build_verification_role(
    allowed_tools=VERIFICATION_TOOLS_READONLY,
    system_prefix_extra=PMO_VERIFICATION_EXTRA,
)

# 兼容旧名
PMO_AUDITOR_ROLE = PMO_VERIFICATION_ROLE


def pmo_verification_audit_enabled() -> bool:
    """PMO 阶段二对抗性验证是否开启（默认关，省 Token）。"""
    raw = (__import__("os").environ.get("PMO_ENABLE_VERIFICATION_AUDIT") or "0").strip().lower()
    return raw in ("1", "true", "yes", "on")
