"""
智能化 P2：修正意图（P2-7）、意图-技能统计（P2-8）配置与入口。

- 配置：`~/.jachin/nexus_config.json` → `intelligence_p2`
- 修正记忆写入：经 `add_local_memory` → **Memory Nexus（SQLite + FastEmbed）**；可选 v8 向量碎片（`core_memory`）
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_NEXUS = Path.home() / ".jachin" / "nexus_config.json"

# 与梦境排序、Prompt 一致的标记前缀（勿改，否则 dream 优先级失效）
CORRECTION_TEXT_PREFIX = "【用户修正】【p2】"


def get_intel_p2_config() -> dict[str, Any]:
    try:
        if not _NEXUS.exists():
            return {}
        cfg = json.loads(_NEXUS.read_text(encoding="utf-8"))
        sec = cfg.get("intelligence_p2")
        return sec if isinstance(sec, dict) else {}
    except Exception as e:
        logger.debug("[P2] 读取 intelligence_p2 失败: %s", e)
        return {}


def is_correction_detection_enabled() -> bool:
    return get_intel_p2_config().get("correction_detection_enabled", True) is not False


def _correction_patterns() -> list[str]:
    cfg = get_intel_p2_config()
    raw = cfg.get("correction_keywords")
    if isinstance(raw, list) and raw:
        return [str(x) for x in raw if str(x).strip()]
    return [
        r"不对",
        r"不是(?:这样|的)?",
        r"搞错了",
        r"你说错了",
        r"理解错了",
        r"应该是",
        r"我要的是",
        r"正确(?:的)?(?:是|说法)",
        r"别胡说",
        r"重写",
        r"不是这样的",
        r"搞反了",
    ]


def detect_correction(user_text: str) -> bool:
    """是否像用户在纠正助手。"""
    if not is_correction_detection_enabled():
        return False
    t = (user_text or "").strip()
    min_len = int(get_intel_p2_config().get("correction_min_user_chars", 4) or 4)
    if len(t) < min_len:
        return False
    for pat in _correction_patterns():
        try:
            if re.search(pat, t, re.IGNORECASE):
                return True
        except re.error:
            continue
    return False


def extract_correction_expectation(user_text: str) -> str:
    """从用户话里抽「期望结论」；抽不到则用全文截断。"""
    t = (user_text or "").strip()
    max_len = int(get_intel_p2_config().get("correction_max_chars", 800) or 800)
    patterns = [
        r"应该是[:：]?\s*(.+)",
        r"我要的是[:：]?\s*(.+)",
        r"正确(?:的)?(?:是|说法)[:：]?\s*(.+)",
        r"应该是\s+(.+)",
    ]
    for pat in patterns:
        m = re.search(pat, t, re.DOTALL | re.IGNORECASE)
        if m:
            chunk = m.group(1).strip()
            if len(chunk) >= 2:
                return chunk[:max_len]
    return t[:max_len]


def format_correction_memory(user_text: str) -> str:
    exp = extract_correction_expectation(user_text)
    return f"{CORRECTION_TEXT_PREFIX}\n期望：{exp}\n原话摘要：{user_text.strip()[:400]}"


def maybe_record_user_correction(user_text: str) -> None:
    """
    run_agent 收到用户句后调用：检测修正意图并落盘。
    """
    if not detect_correction(user_text):
        return
    formatted = format_correction_memory(user_text)
    try:
        from l3_node.local_memory import add_local_memory

        add_local_memory("correction", formatted, source="p2-7")
    except ImportError:
        pass
    except Exception as e:
        logger.debug("[P2-7] Memory Nexus 未写入: %s", e)

    cfg = get_intel_p2_config()
    if cfg.get("correction_write_vector_fragment", True) is not False:
        try:
            from core.memory_store import add_memory_fragment

            add_memory_fragment(formatted, is_consolidated=False)
        except Exception as e:
            logger.debug("[P2] add_memory_fragment 失败（可能无 core 上下文）: %s", e)

    logger.info("[P2-7] 已记录用户修正记忆 len=%d", len(formatted))


def dream_prioritize_correction_enabled() -> bool:
    return get_intel_p2_config().get("dream_prioritize_correction", True) is not False


def sort_fragments_correction_first(fragments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """梦境：含修正标记的碎片排在前面，便于 LLM 优先看见。"""

    def _is_corr(f: dict[str, Any]) -> bool:
        tx = str(f.get("text", ""))
        return CORRECTION_TEXT_PREFIX in tx or "【用户修正】" in tx

    def _key(f: dict[str, Any]) -> tuple[int, float]:
        ts = float(f.get("timestamp", f.get("created_at", 0)) or 0)
        return (0 if _is_corr(f) else 1, ts)

    return sorted(fragments, key=_key)


def intent_stats_enabled() -> bool:
    return get_intel_p2_config().get("intent_stats_enabled", True) is not False


def reinforce_search_enabled() -> bool:
    return get_intel_p2_config().get("reinforce_search_enabled", True) is not False


def reinforce_weight() -> float:
    try:
        v = float(get_intel_p2_config().get("reinforce_weight", 0.12))
        return max(0.0, min(0.5, v))
    except (TypeError, ValueError):
        return 0.12


def reinforce_max_boost() -> float:
    try:
        v = float(get_intel_p2_config().get("reinforce_max_boost", 3.0))
        return max(0.5, min(20.0, v))
    except (TypeError, ValueError):
        return 3.0
