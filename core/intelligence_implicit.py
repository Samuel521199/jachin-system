"""
§4.3 隐式学习：跳过 / 停留 / 复述与重复追问 — 统一事件 schema 与检测。

- 事件写入：~/.jachin/logs/intelligence_events.jsonl（与 emit_intelligence_event 一致）
- 消费：`intelligence_e`（`core/intelligence_e_consumer.py`）可按类型累加侧车
- 客户端：POST /api/v2/intelligence/implicit-signal（Memory Growth 消费治理信号）

单一说明文档：docs/IMPLICIT_SIGNALS.md
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

# ---------------------------------------------------------------------------
# 标准 type 字符串（请勿随意改名；intelligence_e 与文档依赖这些键）
# ---------------------------------------------------------------------------
SIGNAL_SKIP = "user_message_skipped"
SIGNAL_DWELL = "user_message_dwell"
SIGNAL_REPEAT_INTENT = "user_repeat_intent"  # 与上一轮 **用户句** 高度相似
SIGNAL_REPEAT_FOLLOWUP = "user_repeat_followup"  # 上一轮为 assistant 后，用户重复/不满追问


def emit_implicit_signal(
    signal: str,
    payload: dict[str, Any] | None = None,
    *,
    source: str = "internal",
) -> None:
    """写入 intelligence_events；payload 内会附带 source。"""
    from core.intelligence_workspace import emit_intelligence_event

    pl = dict(payload or {})
    pl.setdefault("source", source)
    emit_intelligence_event(str(signal), pl)


def analyze_session_implicit(
    user_input: str,
    prior_messages: list[dict[str, Any]],
    *,
    source: str = "agent_core",
) -> list[tuple[str, dict[str, Any]]]:
    """
    根据当前用户输入与历史消息，返回应发射的 (event_type, payload) 列表（不直接写入）。
    prior_messages：不含本轮 user。
    """
    out: list[tuple[str, dict[str, Any]]] = []
    ui = (user_input or "").strip()
    if not ui:
        return out

    # 1) 与上一轮 **用户** 复述（同句反复发）
    prev_user = ""
    for m in reversed(prior_messages):
        if str(m.get("role") or "").lower() == "user":
            prev_user = (m.get("content") or "").strip()
            break
    if prev_user and len(ui) > 10 and len(prev_user) > 10:
        r = SequenceMatcher(None, ui, prev_user).ratio()
        if r >= 0.86:
            out.append(
                (
                    SIGNAL_REPEAT_INTENT,
                    {"ratio": round(r, 4), "snippet": ui[:160], "source": source},
                )
            )

    # 2) 重复追问：上一轮是 assistant，本轮用户话与「再上一轮用户」相似，或短句不满触发
    if prior_messages:
        last = prior_messages[-1]
        if str(last.get("role") or "").lower() == "assistant":
            prev_user_before = ""
            seen_assi = False
            for m in reversed(prior_messages[:-1]):
                role = str(m.get("role") or "").lower()
                if role == "assistant":
                    seen_assi = True
                    continue
                if role == "user":
                    prev_user_before = (m.get("content") or "").strip()
                    break
            if prev_user_before and len(prev_user_before) > 8 and len(ui) > 8:
                r2 = SequenceMatcher(None, ui, prev_user_before).ratio()
                if r2 >= 0.72:
                    out.append(
                        (
                            SIGNAL_REPEAT_FOLLOWUP,
                            {
                                "ratio": round(r2, 4),
                                "kind": "same_question_after_answer",
                                "snippet": ui[:160],
                                "source": source,
                            },
                        )
                    )
            diss = re.search(
                r"为什么|怎么(?:还|这样)|不对|再说[一1]?遍|没懂|不明白|重新(?:说|解释)|还是不行|听不懂",
                ui,
            )
            if diss and len(ui) < 120:
                out.append(
                    (
                        SIGNAL_REPEAT_FOLLOWUP,
                        {
                            "kind": "dissatisfaction_short",
                            "pattern": diss.group(0),
                            "snippet": ui[:160],
                            "source": source,
                        },
                    )
                )

    return out


def apply_session_implicit_events(
    user_input: str,
    prior_messages: list[dict[str, Any]],
    *,
    source: str = "agent_core",
) -> tuple[int, set[str]]:
    """检测并写入事件；同 type+kind 去重。返回 (写入条数, 已发射的 type 集合)。"""
    pairs = analyze_session_implicit(user_input, prior_messages, source=source)
    seen: set[tuple[str, str]] = set()
    emitted_types: set[str] = set()
    n = 0
    for typ, pl in pairs:
        kind = str(pl.get("kind", "") or "")
        key = (typ, kind)
        if key in seen:
            continue
        seen.add(key)
        emit_implicit_signal(typ, pl, source=source)
        emitted_types.add(typ)
        n += 1
    return n, emitted_types
