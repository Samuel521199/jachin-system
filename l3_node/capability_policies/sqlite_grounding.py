"""SQLite/data grounding guards owned by the database capability layer."""

from __future__ import annotations

import logging
import re
from typing import Any

from l3_node.engine.hooks_pipeline import PipelineContext
from l3_node.primitives.tools.loader import tool_entry_looks_like_sqlite_family

logger = logging.getLogger(__name__)


def tools_include_sqlite_mcp(tools: list[dict[str, Any]] | None) -> bool:
    return any(tool_entry_looks_like_sqlite_family(t) for t in (tools or []))


def last_non_system_user_text(messages: list[dict[str, Any]], *, max_scan: int = 32) -> str:
    seen_user = 0
    for m in reversed(messages or []):
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        seen_user += 1
        if seen_user > max_scan:
            break
        c = str(m.get("content") or "").strip()
        if not c:
            continue
        if c.startswith(("【系统校验·SQLite】", "【系统校验】", "【系统纠偏】", "【strict】")):
            continue
        return c
    return ""


def user_text_requests_workspace_sqlite_verification(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    tl = t.lower()
    if ".sqlite" in tl or "test_db" in tl:
        return True
    if re.search(r"sqlite|\.db\b", tl) and re.search(r"工作区|workspace|查一下|查询|查库", t, re.I):
        return True
    if re.search(r"工作区|workspace", t, re.I) and re.search(r"数据库|缺货|库存", t, re.I):
        return True
    return False


def final_answer_is_honest_sqlite_capability_denial(text: str) -> bool:
    s = text or ""
    if len(s) < 24:
        return False
    return bool(
        re.search(
            r"(无法|不能)(?:真实)?(?:访问|查询).{0,64}(?:test_db|\.sqlite|数据库)|"
            r"不具备.{0,40}(?:执行)?(?:数据库)?(?:读取|查询)|"
            r"(?:可用|可见)?工具列表.{0,48}未包含|"
            r"未包含.{0,56}(?:read_query|SQLite|sqlite)|"
            r"如实说明|不能编造|严禁编造|幻觉输出|并未(?:真正)?查询|"
            r"技能白名单.{0,40}(?:未|没有|不包含).{0,20}(?:权限|read)|"
            r"无法对数据库内容进行",
            s,
            re.I,
        )
    )


def final_answer_claims_sqlite_was_queried(text: str) -> bool:
    s = text or ""
    if final_answer_is_honest_sqlite_capability_denial(s):
        return False
    if re.search(
        r"根据\s*[`\u2018\u2019']?\s*[\w./\\-]+\.sqlite[`\u2018\u2019']?\s*[^。\n]{0,48}"
        r"(?:数据库的查询结果|的查询结果)",
        s,
        re.I,
    ):
        return True
    if re.search(r"根据.{0,32}\.sqlite", s, re.I) and re.search(
        r"(?:数据库的)?实际查询|查询结果|查询表明|查询显示",
        s,
        re.I,
    ):
        return True
    if re.search(r"数据库的查询结果\s*[，,：:]", s) and re.search(
        r"(?:缺货|以下水果|水果现在|库存).{0,24}(?:是|：|:|[\n\r][\s\-•·])",
        s,
    ):
        return True
    if re.search(r"`[^`]*\.sqlite`", s, re.I) and re.search(
        r"(?:数据库的查询结果|的查询结果|查询表明|查询显示|实际查询|数据库的实际查询)",
        s,
        re.I,
    ):
        head2 = s[: min(320, len(s))]
        if not re.search(r"无法|不能|不具备|未包含|并未", head2, re.I):
            return True
    if re.search(r"\.sqlite|`[^`]*\.sqlite`", s, re.I) and re.search(
        r"(?:缺货|库存|stock|quantity).{0,6}(?:是|为|：|:|\=)",
        s,
        re.I,
    ):
        if not final_answer_is_honest_sqlite_capability_denial(s[:280]):
            return True
    return False


def build_sqlite_has_tool_denial_prompt() -> str:
    return (
        "【系统校验·SQLite】本轮工具列表**已包含** MCP SQLite（常见为 **mcp:query**、mcp:read_records、"
        "mcp:list_tables；若有官方 read_query 则为 **mcp:read_query**）。"
        "你在未产生任何 Observation 的情况下写「无法查询 / 没有 SQLite 工具」是**错误**的。\n"
        "请立即输出 ReAct（禁止本轮直接 Final Answer）：\n"
        "Thought: …\n"
        "Action: mcp:list_tables\n"
        "Action Input: {}\n"
        "再根据返回的表结构编写**只读** SELECT，Action: mcp:query，"
        'Action Input: {"sql":"<你的 SELECT；键名须为 sql（mcp-sqlite），勿与 read_query 的 query 混淆>"}\n'
        "取得 Observation 后再 Final Answer。"
    )


def build_sqlite_requires_observation_prompt() -> str:
    return (
        "【系统校验·SQLite】当前问题依赖数据库中的**可核验事实**。"
        "在尚未产生任何工具 Observation 前，**禁止**用 Final Answer 输出具体缺货品类、库存结论或仅一行名称"
        "（会被视为未查库臆测）。\n"
        "必须输出 ReAct：先 **Action: mcp:list_tables**，**Action Input: {}**；再 **Action: mcp:query**，"
        "用只读 SQL（JSON 键 **sql**）查询；**仅当** Observation 返回后才能在 Final Answer 中归纳结果。"
    )


def build_sqlite_fake_query_prompt(has_sqlite: bool) -> str:
    if has_sqlite:
        return (
            "【系统校验·SQLite】你尚未调用任何工具，却声称「根据 *.sqlite / 实际查询」作答——这是禁止的幻觉。\n"
            "当前已注册 **mcp:query** 等，必须先 **mcp:list_tables**（Action Input: {}）确认真实表与列名，"
            "再 **mcp:query** 用只读 SQL（键 **sql**）查询，**禁止**在未收到 Observation 前写「根据…实际查询」"
            "或编造行级结果；也不得用极简 Final Answer 代替工具调用。"
        )
    return (
        "【系统校验·SQLite】你尚未调用任何工具，却声称「根据 test_db.sqlite / 数据库查询结果」作答——这是禁止的幻觉。"
        "当前**可见工具列表中未包含** MCP SQLite（read_query）或已被白名单过滤，你**无法**真实查库。"
        "请在本轮 Final Answer 中**如实说明**无法访问该库，并提示检查：official-sqlite-npx MCP、"
        "SUB_ACCOUNT 技能白名单是否包含 mcp:read_query、以及 db 路径是否指向 ~/.jachin/workspace/test_db.sqlite；"
        "**禁止**编造查询结果或列举具体水果库存。"
    )


def reject_ungrounded_sqlite_final_answer(
    ctx: PipelineContext,
    messages: list[dict[str, Any]],
    response: str,
    ans: str,
    *,
    via: str,
) -> bool:
    """Reject final answers that skipped SQLite tools for DB-grounded questions."""

    skills = ctx.metadata.get("_skills_unfiltered") or ctx.metadata.get("_skills") or []
    last_u = ""
    sqlite_user_turn = False
    for m in reversed(messages):
        if isinstance(m, dict) and m.get("role") == "user":
            c = str(m.get("content") or "")
            if not last_u:
                last_u = c
            if user_text_requests_workspace_sqlite_verification(c):
                sqlite_user_turn = True
    probe = f"{ctx.intent or ''}\n{last_u}"
    if not sqlite_user_turn and not user_text_requests_workspace_sqlite_verification(probe):
        return False
    anchor_u = last_non_system_user_text(messages) or last_u
    latest_sqlite_verify = user_text_requests_workspace_sqlite_verification(anchor_u)
    ans_s = str(ans or "")
    rtrace = str(ctx.metadata.get("_react_step_trace") or "")
    has_sqlite = tools_include_sqlite_mcp(skills)
    inv = int(ctx.metadata.get("_react_tool_invocations") or 0)

    if (
        inv < 1
        and has_sqlite
        and re.search(
            r"(?:抱歉|很抱歉)?[,，]?\s*(?:我)?(?:无法|不能)(?:真实)?(?:查询|访问).{0,96}(?:test_db|\.sqlite|数据库)|"
            r"没有(?:可用)?的\s*(?:SQLite|sqlite)?\s*查询工具|"
            r"(?:当前)?(?:会话)?(?:环境)?中?\s*没有(?:可用)?的.{0,32}(?:SQLite|sqlite|查询工具|数据库)|"
            r"(?:工具|白名单|权限).{0,40}(?:未|没有|不包含).{0,48}(?:read_query|SQLite|sqlite|数据库)|"
            r"没有可用的 SQLite|不具备.{0,24}(?:查询)?(?:数据库|SQLite)",
            ans_s,
            re.I | re.DOTALL,
        )
    ):
        logger.warning(
            "[CapabilityHook][sqlite_grounding] trace=%s via=%s mounted sqlite but model denied access",
            rtrace,
            via,
        )
        messages.append({"role": "assistant", "content": response})
        messages.append({"role": "user", "content": build_sqlite_has_tool_denial_prompt()})
        return True

    if inv < 1 and has_sqlite and latest_sqlite_verify and not final_answer_is_honest_sqlite_capability_denial(ans_s):
        logger.warning(
            "[CapabilityHook][sqlite_grounding] trace=%s via=%s final answer before sqlite observation preview=%r",
            rtrace,
            via,
            ans_s[:160],
        )
        messages.append({"role": "assistant", "content": response})
        messages.append({"role": "user", "content": build_sqlite_requires_observation_prompt()})
        return True

    if not final_answer_claims_sqlite_was_queried(ans_s):
        return False
    if inv >= 1:
        return False
    logger.warning(
        "[CapabilityHook][sqlite_grounding] trace=%s via=%s fake sqlite query claim has_sqlite=%s",
        rtrace,
        via,
        has_sqlite,
    )
    messages.append({"role": "assistant", "content": response})
    messages.append({"role": "user", "content": build_sqlite_fake_query_prompt(has_sqlite)})
    return True
