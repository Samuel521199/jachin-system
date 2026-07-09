"""Action critique role support for the Cognitive Kernel.

Critique is a role-agent function that reviews proposed actions before external
side effects. It is not the architecture root. Architecture SSOT:
docs/07_memory_first_main_agent_and_voice_app_agents.md
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)


def action_critic_enabled() -> bool:
    v = (os.environ.get("JACHIN_ACTION_CRITIC_ENABLED") or "1").strip().lower()
    return v in ("1", "true", "yes", "on")


def action_critic_max_fails() -> int:
    try:
        return max(1, min(16, int(os.environ.get("JACHIN_ACTION_CRITIC_MAX_FAILS") or "3")))
    except (TypeError, ValueError):
        return 3


def critic_model_litellm_id() -> str:
    raw = (os.environ.get("JACHIN_CRITIC_MODEL") or "").strip()
    if raw:
        try:
            from l3_node.intent_gateway.model_resolve import _to_litellm_id

            return _to_litellm_id(raw)
        except Exception:
            if raw.lower().startswith("qwen") and "/" not in raw:
                return f"dashscope/{raw}"
            return raw
    try:
        from l3_node.intent_gateway.model_resolve import get_classification_model_litellm_id

        return get_classification_model_litellm_id()
    except Exception:
        return "dashscope/qwen-turbo"


def _strip_json_fence(text: str) -> str:
    s = (text or "").strip()
    if not s.startswith("```"):
        return s
    s = re.sub(r"^```\w*\s*", "", s)
    s = re.sub(r"\s*```\s*$", "", s).strip()
    return s


def _normalize_mcp_tool_id(tool_id: str) -> str:
    t = (tool_id or "").strip().lower()
    if t.startswith("mcp:"):
        t = t[4:].strip().lower()
    return t


def _parse_action_input_json(action_input: Any) -> dict[str, Any]:
    if isinstance(action_input, dict):
        return action_input
    s = (action_input or "").strip()
    if not s:
        return {}
    try:
        o = json.loads(s)
        return o if isinstance(o, dict) else {}
    except json.JSONDecodeError:
        return {}


# 明显「日常偏好 / 项目情报」用语（非安防禁令）；与高危词互斥后用于打回误用 safety_lock_append
_SOFT_PREF_HINT_RE = re.compile(
    r"(偏好|习惯|口味|饮食|喜欢吃|框架|技术栈|frontend|backend|litestar|fastapi|django|flask|vue|react|svelte|"
    r"项目代号|代号|日常|喜好|喜欢用|默认用)",
    re.I,
)
_SAFETY_RISK_HINT_RE = re.compile(
    r"(禁止|严禁|不得|不允许|高危|生产环境|删库|DROP\s+TABLE|TRUNCATE|credential|凭据|密码|token|密钥|"
    r"rm\s+-rf|chmod\s+777|iptables|sudo)",
    re.I,
)


def _safety_lock_body_looks_like_soft_preference(body: str) -> bool:
    s = (body or "").strip()
    if not s or len(s) > 800:
        return False
    if _SAFETY_RISK_HINT_RE.search(s):
        return False
    return bool(_SOFT_PREF_HINT_RE.search(s))


def _extract_sql_from_proposed(proposed_action: dict[str, Any]) -> str:
    obj = _parse_action_input_json(proposed_action.get("action_input"))
    for k in ("sql", "query", "statement"):
        v = obj.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


_DML_PATTERN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|REPLACE|CREATE\s+TABLE|ATTACH|DETACH)\b",
    re.I | re.DOTALL,
)


def _is_select_only_sql(sql: str) -> bool:
    """粗判：单条语句视角下仅为只读查询（允许 WITH…SELECT）。"""
    s = (sql or "").strip()
    if not s:
        return False
    # 取第一条语句（分号截断）
    first = s.split(";")[0].strip()
    if not first:
        return False
    if _DML_PATTERN.search(first):
        return False
    if not re.search(r"\bSELECT\b", first, re.I):
        return False
    return True


def _sqlite_action_kind(tool_id: str, sql: str) -> str:
    """将 SQLite 族工具粗分为 read | write | unknown（unknown 交 LLM）。"""
    r = _normalize_mcp_tool_id(tool_id)
    if r in ("list_tables", "get_table_schema", "db_info", "read_records"):
        return "read"
    if r in ("write_query", "create_record", "update_records", "delete_records"):
        return "write"
    if r == "read_query":
        if not (sql or "").strip():
            return "unknown"
        return "read" if _is_select_only_sql(sql) else "write"
    if r == "query":
        if not (sql or "").strip():
            return "unknown"
        return "read" if _is_select_only_sql(sql) else "write"
    if "write_query" in r:
        return "write"
    if "read_query" in r and "write" not in r:
        return "read"
    return "unknown"


def _sql_first_statement_is_insert(sql: str) -> bool:
    """首条语句是否为 INSERT（新建表后首行写入、记账场景）。"""
    s = (sql or "").strip()
    if not s:
        return False
    first = s.split(";")[0].strip()
    return bool(re.match(r"^\s*INSERT\b", first, re.I))


def _action_has_jachin_mcp_write_ack(proposed_action: dict[str, Any]) -> bool:
    ai = _parse_action_input_json(proposed_action.get("action_input"))
    v = ai.get("jachin_mcp_write_ack")
    if v is True:
        return True
    if isinstance(v, str) and v.strip().lower() in ("1", "true", "yes", "on"):
        return True
    return False


def _observation_hints_post_ddl_ready_for_insert(excerpt: str) -> bool:
    """上一轮 Observation 是否表明刚建表成功，允许紧接着 INSERT 首行（无需先 SELECT 出数据行）。"""
    ex = excerpt or ""
    if "Table created successfully" in ex:
        return True
    if re.search(r"CREATE\s+TABLE", ex, re.I) and re.search(r"success", ex, re.I):
        return True
    return False


def _observation_excerpt_suggests_prior_rowset(excerpt: str) -> bool:
    """上一轮用户侧 Observation 是否像已成功返回行数据（供写步放行）。"""
    s = (excerpt or "").strip()
    if len(s) < 24:
        return False
    if "System Critic Error" in s[:500]:
        return False
    idx = s.find("Observation:")
    tail = s[idx + len("Observation:") :] if idx >= 0 else s
    tail = tail[:4000]
    if "MCP 工具错误" in tail[:400] or "-32602" in tail[:400]:
        return False
    if "[{" in tail or (tail.strip().startswith("[") and '"name"' in tail):
        return True
    if re.search(r'"name"\s*:|"count"\s*:|"price"\s*:', tail):
        return True
    return False


def _critic_deterministic_pass(
    user_intent: str,
    proposed_action: dict[str, Any],
    *,
    react_observation_excerpt: str,
) -> tuple[bool, str] | None:
    """
    返回 (True, "") 表示确定性放行；(False, critique) 表示确定性打回；None 表示交 LLM。
    """
    tid = str(proposed_action.get("tool_id") or "").strip()
    if not tid:
        return None
    if tid == "core:safety_lock_append":
        ai = _parse_action_input_json(proposed_action.get("action_input"))
        body = str(ai.get("body") or ai.get("content") or ai.get("text") or "").strip()
        if body and _safety_lock_body_looks_like_soft_preference(body):
            return (
                False,
                (
                    "打回！本条属于日常偏好/项目情报，禁止使用 core:safety_lock_append。"
                    "请改用 **core:local_memory_append**（content JSON）写入 Memory Nexus，或 **core:local_memory_search** / **recall_memory**（同源 Nexus）检索；"
                    "仅「禁止高危操作、核心安防」才允许 safety_lock_append。"
                ),
            )
    sql = _extract_sql_from_proposed(proposed_action)
    kind = _sqlite_action_kind(tid, sql)
    if kind == "read":
        return True, ""
    # 写库：INSERT 且用户已显式 ack，或紧接在「建表成功」之后（空表首行 / 本地记账）
    if kind == "write" and sql and _sql_first_statement_is_insert(sql):
        if _action_has_jachin_mcp_write_ack(proposed_action):
            return True, ""
        if _observation_hints_post_ddl_ready_for_insert(react_observation_excerpt):
            return True, ""
    if kind == "write" and _observation_excerpt_suggests_prior_rowset(react_observation_excerpt):
        return True, ""
    return None


def _parse_critic_response(text: str) -> tuple[bool, str]:
    raw = _strip_json_fence(text)
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            logger.warning("[ActionCritic] 无法解析 JSON，放行。preview=%r", raw[:200])
            return True, ""
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            logger.warning("[ActionCritic] JSON 二次解析失败，放行。preview=%r", raw[:200])
            return True, ""
    if not isinstance(obj, dict):
        return True, ""
    ok = bool(obj.get("ok", True))
    critique = str(obj.get("critique") or obj.get("reason") or obj.get("message") or "").strip()
    return ok, critique


async def evaluate_action(
    user_intent: str,
    proposed_action: dict[str, Any],
    semantic_layer: dict[str, Any],
    *,
    react_observation_excerpt: str = "",
) -> tuple[bool, str]:
    """
    使用轻量模型审查 proposed_action。

    Returns:
        (True, "") 表示通过；
        (False, critique) 表示拦截，critique 为给 Actor 的中文改正建议。
    """
    if not action_critic_enabled():
        return True, ""

    _exo = (react_observation_excerpt or "").strip()
    try:
        _det = _critic_deterministic_pass(
            user_intent, proposed_action, react_observation_excerpt=_exo
        )
        if _det is not None:
            _ok, _crit = _det
            if _ok:
                return True, ""
            return False, _crit
    except Exception as e:
        logger.debug("[ActionCritic] deterministic_pass 跳过: %s", e)

    try:
        model = critic_model_litellm_id()
        system = (
            "你是 Jachin AI OS 的 Action Critic（逻辑审查员）。"
            "只输出 **一个** JSON 对象，禁止 Markdown 代码围栏或其它文字。\n"
            "Schema: {\"ok\": boolean, \"critique\": string}\n"
            "- ok=true：动作与意图一致，或信息不足但无明显逻辑错误；critique 必须是空字符串。\n"
            "- ok=false：发现明显逻辑错误、跳步、或高风险 SQL 时；critique 用简体中文写出**给 Actor 的下一步行动指令**（必须具体、可立刻照做，禁止含糊）。\n"
            "\n"
            "【绝对审查纪律】\n"
            "1) 你的对话对象是**内部的执行系统 (Actor)**，绝不是终端用户；critique 里不要写「告诉用户」「请用户…」之类。\n"
            "2) 当 user_intent 需要【先查询(Read)、后修改(Write)】的多步任务时：若本步 proposed_action 是 write_query 却明显缺少前置 read 依据、"
            "或试图在未知行/未知主键的情况下盲写 UPDATE/DELETE，必须 ok=false 并打回。\n"
            "3) **致命禁令**：critique 中**绝对禁止**出现下列摆烂措辞（含同义改写）："
            "「建议人工核查」「请人工」「向用户确认」「让用户确认」「无法安全自动执行」「建议联系管理员」「交由人工」等。"
            "禁止把任务推给人类；你必须命令 Actor 在工具链内**连续**自主完成（先 SELECT（mcp:query/sql 或 read_query）拿 Observation，再在同一思考链路内立刻写操作：mcp:update_records 或 write_query 或 mcp:query 带 DML，不准中断对话）。\n"
            "【工具名等价】实际工具 id 可能是 mcp:query、mcp:read_records、mcp:update_records、mcp:list_tables、mcp:get_table_schema、read_query、write_query 等，"
            "**不得**仅因名称不是 read_query/write_query 就打回；mcp:query 的 sql 为 SELECT 即只读，为 UPDATE/INSERT/DELETE 即写路径。\n"
            "【上下文】payload 内 `react_observation_excerpt` 若为上一轮**真实** Observation（含查询返回的行），则随后的写操作**不得**再以「尚未执行查询」为由判 ok=false。\n"
            "4) 打回时必须给出**明确行动指令**，例如："
            "「打回！你必须先调用 mcp:query（或 read_query / mcp:read_records）执行 SELECT 查出具体数据。拿到 Observation 后，**紧接着在本次思考链路中立刻输出下一个 Action（mcp:update_records 或 write_query 或 mcp:query 的 UPDATE），绝对不准中断对话！**」"
            "「打回！必须先 mcp:list_tables / mcp:query(SELECT) 确认列名与主键，再在同一思考链路内连续输出写 Action，不得中途 Final Answer。」\n"
            "5) **最高豁免权**：当 user_intent 是【先查后改】时，若 Actor 当前 action 为**合法 SELECT 查询**（无任何 UPDATE/DELETE/DROP 等写操作语义），"
            "说明正在正确执行第一步！你**必须、立刻判定 ok=true**（critique 空字符串）；**绝对禁止**以「还没执行修改」「任务未完成」为由打回。\n"
            "（补充：list_tables、PRAGMA table_info、mcp:read_records 等纯只读探查，在【先查后改】场景下同样必须 ok=true。）\n"
            "\n"
            "审查要点（技术）：\n"
            "A) proposed_action 中的 SQL/查询是否匹配 user_intent（如「缺货」「低库存」「最贵」等）。\n"
            "B) 若 semantic_layer 非空，业务词是否应按其中的片段体现为 WHERE/ORDER BY 等，而不是 SELECT * 拉全表再在脑中筛选。\n"
            "C) 只要 proposed_action 是**纯只读**（SELECT、list_tables、describe_table、PRAGMA table_info、只读 MCP 查询等），且**没有**试图盲目 INSERT/UPDATE/DELETE/write，"
            "一律 ok=true（critique 空字符串）。\n"
            "D) 不要臆造表名；若仅缺 Schema，可 ok=true；若 Actor 在缺 Schema 时直接写破坏性 DML，必须 ok=false 并按纪律 4 命令其先探查/只读。\n"
            "6) **core:safety_lock_append 专用**：若 action 内容仅为日常偏好、项目代号、框架喜好、饮食习惯等非安防信息，必须 ok=false，"
            "命令 Actor 改用 **core:local_memory_append**（Memory Nexus）或 **core:local_memory_search** / **recall_memory**；仅「禁止高危操作、核心底层安防」才允许本工具。"
        )
        payload = {
            "user_intent": (user_intent or "")[:6000],
            "proposed_action": proposed_action,
            "semantic_layer": semantic_layer or {},
            "react_observation_excerpt": _exo[:6000] if _exo else "",
        }
        user = (
            "请审查以下 JSON（整段即上下文）。\n"
            + json.dumps(payload, ensure_ascii=False, indent=2)[:24000]
        )

        try:
            import litellm
        except ImportError:
            logger.debug("[ActionCritic] litellm 未安装，放行")
            return True, ""

        try:
            from l3_node.llm_client import _effective_max_tokens_for_model

            max_t = _effective_max_tokens_for_model(model, 512)
        except Exception:
            max_t = 512

        timeout = 45.0
        try:
            timeout = float(os.environ.get("JACHIN_ACTION_CRITIC_TIMEOUT_SEC") or "45")
        except (TypeError, ValueError):
            pass

        try:
            kw: dict[str, Any] = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.0,
                "max_tokens": max_t,
                "timeout": timeout,
                "stream": False,
            }
            try:
                from core.llm_provider import _inject_api_keys
                from core.brain.llm.dashscope_regional import litellm_apply_dashscope_credentials

                _inject_api_keys()
                litellm_apply_dashscope_credentials(model, kw)
            except ImportError:
                pass
            resp = await litellm.acompletion(**kw)
        except Exception as e:
            logger.warning("[ActionCritic] LLM 调用失败/超时，fail-open 放行: %s", e)
            return True, ""

        try:
            choice0 = resp.choices[0] if resp and getattr(resp, "choices", None) else None
            msg = getattr(choice0, "message", None) if choice0 else None
            content = (getattr(msg, "content", None) or "") if msg else ""
            if isinstance(content, list):
                content = "".join(
                    str(x.get("text", x)) if isinstance(x, dict) else str(x) for x in content
                )
        except Exception as e:
            logger.warning("[ActionCritic] 读取响应失败，放行: %s", e)
            return True, ""

        ok, critique = _parse_critic_response(str(content))
        if ok:
            return True, ""
        if not critique:
            critique = (
                "打回！按 L4 SOP：先只读（mcp:query+SELECT、mcp:read_records、list_tables）拿 Observation；"
                "随后在同一思考链路内紧接着输出写操作（mcp:update_records、write_query 或 mcp:query+UPDATE），"
                "禁止跳过查询直接盲写，禁止中断对话或输出 Final Answer 等人下指令。"
            )
        return False, critique
    except Exception as e:
        logger.warning("[ActionCritic] 未预期异常，fail-open 放行: %s", e)
        return True, ""
