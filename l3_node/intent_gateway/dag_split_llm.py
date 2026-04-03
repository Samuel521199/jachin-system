"""
复合意图 LLM 拆分：dependency_analysis（CoT）+ sub_intents + 参数绑定；
反「谄媚型优化」系统提示；depends_on 与 preconditions 合并后交 topology 校验（含环即拒）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, List, Optional, Tuple

from l3_node.intent_gateway.envelope import LocalityHint, SubIntentNode

logger = logging.getLogger(__name__)

_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)

_LOCALITY_OK: set[str] = {
    "local_only",
    "prefer_l2",
    "require_l2_task_manager",
    "edge_sensor",
    "unspecified",
}

DAG_SPLIT_SYSTEM_PROMPT = """你是一个无情的语法依赖提取器，只做结构化抽取，不做业务可行性判断。

【最高优先级 — 反脑补】
- 绝对不允许擅自优化、理顺或「修复」用户描述里的逻辑，即使会形成悖论、死锁或循环依赖。
- 若用户明确表示「动作 A 需要 B 的产物 / 前置条件 / 先发生 B」，则必须在 A 的 depends_on 中列出对应子意图 id（B 的那一项）。
- 你的职责是如实反映用户陈述的依赖拓扑；合法性、能否执行、是否存在死锁，全部由下游 Validator 负责，你不得代劳删减边。
- 不得遗漏用户明确提出的约束条件（例如：某步需要邮件里的授权码、某步依赖另一步的输出）。

【输出格式】
只输出一个 JSON 对象（不要 Markdown 围栏外的解释文字），顶层必须包含：
1) dependency_analysis：数组。每一项描述「某子意图在开工前，用户要求必须先具备什么」，字段：
   - intent_ref：字符串，对应 sub_intents[].id 或对该步骤的简短指代
   - verbatim_prerequisites：字符串，用用户原话或直白复述前提（不要概括成「准备好材料」这种空话）
   - blocked_until_sub_intents：字符串数组，列出必须先完成的子意图 id（若用户明确说了顺序/依赖）
2) sub_intents：数组。每一项字段：
   - id：字符串，必须从 sub_0 起连续编号：sub_0, sub_1, sub_2, …
   - text_span：从用户原文切出的子句（尽量照录）
   - rewritten_text：可读的独立意图句（保留所有约束细节）
   - what：极短动作标签（英文 snake_case 或中文短语均可）
   - locality：仅允许 local_only | prefer_l2 | require_l2_task_manager | edge_sensor | unspecified
   - depends_on：字符串数组，列出必须先完成的子意图 id；无则 []
   - planning_requirement：none | optional | mandatory
   - preconditions：数组，每项含 param（参数/产物名）、from_sub_intent（产出方子意图 id）、relation（如 output_of / mentioned_in）、user_evidence（用户依据短语）
   - slot_schema（可选）：数组，每项为槽位定义，与 Registry required_slots 同形——name（必填）、pattern（正则，推荐）、prompt_template（追问模板）、description（可选）；亦可含 enum（字符串数组）表达允许取值（下游可转为正则）

【参数绑定】
若某参数（如 auth_code）明确来自另一子意图的产物或用户指明「在邮件/某步里」，必须把 from_sub_intent 写对，并在消费该参数的子意图的 depends_on 中包含该子意图 id（除非用户明确说可并行且无需等待——此时 depends_on 可为空，但须在 dependency_analysis 中写清理由）。"""


def _parse_json_loose(raw: str) -> Optional[dict[str, Any]]:
    s = (raw or "").strip()
    if not s:
        return None
    m = _JSON_FENCE.search(s)
    if m:
        s = m.group(1).strip()
    try:
        o = json.loads(s)
        return o if isinstance(o, dict) else None
    except json.JSONDecodeError:
        return None


def merge_preconditions_into_depends_on(nodes: List[SubIntentNode]) -> None:
    """把 preconditions[].from_sub_intent 并入 depends_on（去重），减少模型漏填边。"""
    id_set = {n.id for n in nodes if n.id}
    for n in nodes:
        for p in n.preconditions or []:
            if not isinstance(p, dict):
                continue
            fid = str(p.get("from_sub_intent") or p.get("from_node") or p.get("source_node_id") or "").strip()
            if fid and fid in id_set and fid not in n.depends_on:
                n.depends_on.append(fid)


def _coerce_locality(v: Any) -> LocalityHint:
    s = str(v or "").strip()
    return s if s in _LOCALITY_OK else "unspecified"  # type: ignore[return-value]


def _dict_to_node(d: dict[str, Any]) -> Optional[SubIntentNode]:
    i = str(d.get("id") or "").strip()
    if not i or not re.match(r"^sub_\d+$", i):
        return None
    ts = str(d.get("text_span") or "").strip()
    rw = str(d.get("rewritten_text") or ts).strip()
    dep_raw = d.get("depends_on") or []
    deps: list[str] = []
    if isinstance(dep_raw, list):
        for x in dep_raw:
            s = str(x).strip()
            if s:
                deps.append(s)
    pr = d.get("preconditions") or []
    preconds: list[dict[str, Any]] = [x for x in pr if isinstance(x, dict)] if isinstance(pr, list) else []
    ss_raw = d.get("slot_schema") or []
    slot_schema: list[dict[str, Any]] = []
    if isinstance(ss_raw, list):
        for x in ss_raw:
            if not isinstance(x, dict):
                continue
            item = dict(x)
            enum_v = item.get("enum")
            if isinstance(enum_v, list) and enum_v and not str(item.get("pattern") or "").strip():
                esc = "|".join(re.escape(str(v).strip()) for v in enum_v if str(v).strip())
                if esc:
                    item["pattern"] = f"(?:{esc})"
            slot_schema.append(item)
    prq = str(d.get("planning_requirement") or "none").strip()
    if prq not in ("none", "optional", "mandatory"):
        prq = "none"
    ex = {k: v for k, v in d.items() if k not in _NODE_KNOWN_KEYS}
    return SubIntentNode(
        id=i,
        text_span=ts,
        rewritten_text=rw or ts,
        what=str(d.get("what") or "").strip() or "sub_intent",
        locality=_coerce_locality(d.get("locality")),
        depends_on=deps,
        planning_requirement=prq,
        rbac_scope_hint=str(d.get("rbac_scope_hint") or "").strip(),
        preconditions=preconds,
        slot_schema=slot_schema,
        extra=ex if ex else {},
    )


_NODE_KNOWN_KEYS = frozenset(
    {
        "id",
        "text_span",
        "rewritten_text",
        "what",
        "locality",
        "depends_on",
        "planning_requirement",
        "rbac_scope_hint",
        "preconditions",
        "slot_schema",
        "is_compensable",
        "compensation_action_id",
    }
)


def parse_dag_split_llm_response(raw: str) -> Tuple[Optional[list[Any]], Optional[List[SubIntentNode]]]:
    """
    返回 (dependency_analysis 原始列表, SubIntentNode 列表)；解析失败返回 (None, None)。
    """
    data = _parse_json_loose(raw)
    if not data:
        return None, None
    da = data.get("dependency_analysis")
    if not isinstance(da, list) or len(da) < 1:
        return None, None
    subs = data.get("sub_intents")
    if not isinstance(subs, list) or len(subs) < 2:
        return None, None
    nodes: List[SubIntentNode] = []
    seen: set[str] = set()
    for item in subs:
        if not isinstance(item, dict):
            return None, None
        n = _dict_to_node(item)
        if n is None or n.id in seen:
            return None, None
        seen.add(n.id)
        nodes.append(n)
    # 依赖必须指向已存在 id
    id_set = {n.id for n in nodes}
    for n in nodes:
        for d in list(n.depends_on):
            if d not in id_set:
                return None, None
    merge_preconditions_into_depends_on(nodes)
    for n in nodes:
        n.depends_on = sorted(set(n.depends_on))
    return da, nodes


async def propose_subintents_via_llm_async(*, user_text: str, engine: Any) -> Tuple[Optional[list[Any]], List[SubIntentNode]]:
    """
    调用小模型拆分；成功返回 (dependency_analysis, nodes)，失败返回 ([], [])。
    """
    from l3_node.intent_gateway.config import get_intent_gateway_config
    from l3_node.intent_gateway.model_resolve import get_classification_model_litellm_id

    cfg = get_intent_gateway_config()
    ui = (user_text or "").strip()
    if len(ui) < 12:
        return None, []
    if len(ui) > 6000:
        ui = ui[:6000]
    try:
        to = float(cfg.get("dag_splitting_llm_timeout_sec", 12.0))
    except (TypeError, ValueError):
        to = 12.0
    try:
        max_tok = int(cfg.get("dag_splitting_llm_max_tokens", 1200))
    except (TypeError, ValueError):
        max_tok = 1200

    messages = [
        {"role": "system", "content": DAG_SPLIT_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": "用户复合指令如下，请只输出 JSON（含 dependency_analysis 与 sub_intents）：\n\n" + ui,
        },
    ]
    model = get_classification_model_litellm_id()

    async def _call() -> str:
        raw = await engine.generate_response(
            messages,
            tools=None,
            temperature=0.0,
            max_tokens=max_tok,
            l3_call_purpose="intent_gateway_dag_split",
            l3_override_model=model,
        )
        if isinstance(raw, dict):
            return (raw.get("content") or "") or ""
        return str(raw or "")

    try:
        text = await asyncio.wait_for(_call(), timeout=to)
    except asyncio.TimeoutError:
        logger.info("[IntentGateway] dag_split LLM 超时 %.1fs", to)
        return None, []
    except Exception as e:
        logger.info("[IntentGateway] dag_split LLM 失败: %s", str(e)[:200])
        return None, []

    da, nodes = parse_dag_split_llm_response(text)
    if not nodes:
        logger.debug("[IntentGateway] dag_split 解析失败或不足 2 节点 raw_head=%s", (text or "")[:120])
        return None, []
    return da if isinstance(da, list) else [], nodes
