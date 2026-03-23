"""
向量级隐式信号（§4.3 加强）：复述 / 同题追问 / 复述助手答复。

依赖 nexus `intelligence_implicit` 与 `core.embedding.get_embedder`；失败时静默跳过。
事件 type：user_repeat_intent_embedding、user_repeat_followup_embedding、user_echo_assistant_embedding
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_NEXUS = Path.home() / ".jachin" / "nexus_config.json"


def _load_cfg() -> dict[str, Any]:
    out: dict[str, Any] = {
        "embedding_signals_enabled": True,
        "embedding_prev_user_threshold": 0.88,
        "embedding_followup_user_threshold": 0.82,
        "embedding_echo_assistant_threshold": 0.80,
        "embedding_max_chars": 512,
        "embedding_skip_if_text_repeat": True,
    }
    if not _NEXUS.exists():
        return out
    try:
        cfg = json.loads(_NEXUS.read_text(encoding="utf-8"))
        sec = cfg.get("intelligence_implicit")
        if isinstance(sec, dict):
            for k, v in sec.items():
                if v is not None:
                    out[k] = v
    except Exception as e:
        logger.debug("[IntelImplicitEmb] 读配置失败: %s", e)
    return out


def _truncate(s: str, n: int) -> str:
    s = (s or "").strip()
    if len(s) <= n:
        return s
    return s[:n]


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na < 1e-12 or nb < 1e-12:
        return 0.0
    return float(dot / (na * nb))


def _prev_user_text(prior: list[dict[str, Any]]) -> str:
    for m in reversed(prior):
        if str(m.get("role") or "").lower() == "user":
            return (m.get("content") or "").strip()
    return ""


def _last_assistant_text(prior: list[dict[str, Any]]) -> str:
    for m in reversed(prior):
        if str(m.get("role") or "").lower() == "assistant":
            return (m.get("content") or "").strip()
    return ""


def _user_before_last_assistant(prior: list[dict[str, Any]]) -> str:
    """紧邻上一轮 assistant 之前的 user 句。"""
    last_a_idx = -1
    for i in range(len(prior) - 1, -1, -1):
        if str(prior[i].get("role") or "").lower() == "assistant":
            last_a_idx = i
            break
    if last_a_idx <= 0:
        return ""
    for j in range(last_a_idx - 1, -1, -1):
        if str(prior[j].get("role") or "").lower() == "user":
            return (prior[j].get("content") or "").strip()
    return ""


async def emit_embedding_implicit_signals(
    user_input: str,
    prior_messages: list[dict[str, Any]],
    *,
    source: str = "agent_core",
    text_emitted_types: set[str] | None = None,
) -> int:
    """
    异步嵌入比对并写 intelligence_events；返回发射条数。
    """
    cfg = _load_cfg()
    if not bool(cfg.get("embedding_signals_enabled", True)):
        return 0

    ui = (user_input or "").strip()
    if len(ui) < 8:
        return 0

    try:
        from core.embedding import get_embedder

        emb = get_embedder()
    except Exception as e:
        logger.debug("[IntelImplicitEmb] embedder 不可用: %s", e)
        return 0

    mx = int(cfg.get("embedding_max_chars", 512) or 512)
    mx = max(64, min(4096, mx))
    t_ui = _truncate(ui, mx)

    try:
        v_ui = await emb.embed_text(t_ui)
    except Exception as e:
        logger.debug("[IntelImplicitEmb] embed user 失败: %s", e)
        return 0
    if not v_ui:
        return 0

    from core.intelligence_workspace import emit_intelligence_event

    th_prev = float(cfg.get("embedding_prev_user_threshold", 0.88) or 0.88)
    th_fu = float(cfg.get("embedding_followup_user_threshold", 0.82) or 0.82)
    th_echo = float(cfg.get("embedding_echo_assistant_threshold", 0.80) or 0.80)

    n = 0
    te = text_emitted_types or set()
    skip_ri = bool(cfg.get("embedding_skip_if_text_repeat", True)) and "user_repeat_intent" in te

    pu = _prev_user_text(prior_messages)
    if not skip_ri and len(pu) >= 8:
        try:
            v_pu = await emb.embed_text(_truncate(pu, mx))
            if v_pu:
                sim = _cosine(v_ui, v_pu)
                if sim >= th_prev:
                    emit_intelligence_event(
                        "user_repeat_intent_embedding",
                        {"cosine": round(sim, 4), "threshold": th_prev, "source": source},
                    )
                    n += 1
        except Exception as e:
            logger.debug("[IntelImplicitEmb] prev_user 比对失败: %s", e)

    if prior_messages and str(prior_messages[-1].get("role") or "").lower() == "assistant":
        ub = _user_before_last_assistant(prior_messages)
        if len(ub) >= 8:
            try:
                v_ub = await emb.embed_text(_truncate(ub, mx))
                if v_ub:
                    sim2 = _cosine(v_ui, v_ub)
                    if sim2 >= th_fu:
                        emit_intelligence_event(
                            "user_repeat_followup_embedding",
                            {
                                "cosine": round(sim2, 4),
                                "threshold": th_fu,
                                "kind": "semantic_same_question",
                                "source": source,
                            },
                        )
                        n += 1
            except Exception as e:
                logger.debug("[IntelImplicitEmb] followup 比对失败: %s", e)

        at = _last_assistant_text(prior_messages)
        if len(at) >= 24:
            try:
                v_at = await emb.embed_text(_truncate(at, mx))
                if v_at:
                    sim3 = _cosine(v_ui, v_at)
                    if sim3 >= th_echo:
                        emit_intelligence_event(
                            "user_echo_assistant_embedding",
                            {
                                "cosine": round(sim3, 4),
                                "threshold": th_echo,
                                "source": source,
                            },
                        )
                        n += 1
            except Exception as e:
                logger.debug("[IntelImplicitEmb] echo assistant 比对失败: %s", e)

    return n
