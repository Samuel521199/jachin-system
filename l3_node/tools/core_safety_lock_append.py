"""
core:safety_lock_append 的分流与 TOFU（Trust On First Use / 同类二次免批）辅助逻辑。

SSOT 写入仍由 jachin_safety_lock.append_verified_fact 调用本模块函数完成「是否免批 / 如何改 MD」。
"""

from __future__ import annotations

import re
from typing import Literal

# 与 jachin_safety_lock._make_block 中片段一致：` · category=`slug``
_CATEGORY_IN_HEADER_RE = re.compile(r"category=`([^`]+)`")


def normalize_safety_lock_category(category: str | None) -> str | None:
    """
    将 category 规范为可稳定写入 MD 的 slug（小写、字母数字与下划线，最长 64）。
    空或无效则返回 None（表示不按 category 参与 TOFU）。
    """
    if category is None:
        return None
    s = str(category).strip().lower()
    if not s:
        return None
    s = re.sub(r"[^a-z0-9_]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    if not s:
        return None
    return s[:64]


def scan_approved_categories_in_text(md_text: str) -> set[str]:
    """从已写入的 JACHIN_SAFETY_LOCK.md 全文解析出曾出现过的 category slug（仅含显式 category=`...` 的条目）。"""
    found: set[str] = set()
    for m in _CATEGORY_IN_HEADER_RE.finditer(md_text or ""):
        n = normalize_safety_lock_category(m.group(1))
        if n:
            found.add(n)
    return found


def remove_lock_blocks_for_category(md_text: str, category_norm: str) -> tuple[str, int]:
    """
    删除「标题行含 category=`category_norm`」的条目块（块以 \\n\\n---\\n\\n 分隔）。
    返回 (新全文, 删除块数)。不碰无 category 的历史块。
    """
    cat_tag = f"category=`{category_norm}`"
    parts = re.split(r"\n\n---\n\n", md_text or "")
    if len(parts) <= 1:
        return md_text, 0
    preamble = parts[0]
    kept: list[str] = [preamble]
    removed = 0
    for block in parts[1:]:
        head = block[:1200]
        if cat_tag in head and "### 条目" in head:
            removed += 1
            continue
        kept.append(block)
    if removed == 0:
        return md_text, 0
    return "\n\n---\n\n".join(kept), removed


AppendPath = Literal["pending", "tofu_auto", "direct_md"]


def decide_safety_lock_append_path(
    *,
    append_requires_approval: bool,
    category_norm: str | None,
    approved_categories: set[str],
) -> AppendPath:
    """
    分流判断（展示用 / 单测用）：

    - direct_md：配置为 direct_append_to_md，不经 pending。
    - tofu_auto：需要审批流程已开启，但提供了 category，且该 category 已在正式 MD 中出现过（首条已人工批过）→ 同类二次自动写入并覆盖旧块。
    - pending：新 category 或未提供 category → 仍走待审批队列。
    """
    if not append_requires_approval:
        return "direct_md"
    if category_norm and category_norm in approved_categories:
        return "tofu_auto"
    return "pending"


def format_category_header_fragment(category_norm: str | None) -> str:
    """供 _make_block 拼接标题行；无 category 时返回空串。"""
    if not category_norm:
        return ""
    return f" · category=`{category_norm}`"
