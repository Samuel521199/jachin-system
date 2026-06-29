"""Lightweight semantic retrieval for capability descriptors.

This is intentionally dependency-free.  It behaves like a small lexical
embedding layer: normalize user text, expand common office/OS synonyms, score
capability descriptors, and return explainable matches.  A real vector backend
can replace the scorer behind the same API later.
"""
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from l3_node.capability_semantic_registry import CapabilityDescriptor


_SYNONYMS: dict[str, tuple[str, ...]] = {
    "send_message": ("发给", "发送", "通知", "告诉", "转给", "发消息", "发到", "deliver", "notify"),
    "lark": ("lark", "飞书", "联系人", "群聊", "Neil", "Vivian", "Samuel"),
    "codex": ("codex", "代码分析", "项目总结", "读取项目", "开发进展"),
    "project": ("项目", "工程", "代码库", "仓库", "repo", "Jachin", "目录"),
    "summary": ("总结", "整理", "简报", "汇报", "最近", "进展", "新功能", "做了什么", "干了啥"),
    "app": ("打开", "启动", "切换", "聚焦", "窗口", "app", "应用"),
    "file": ("文件", "目录", "桌面", "下载", "文档", "复制", "移动", "重命名", "删除", "上传", "附件"),
    "system": ("系统", "电脑", "磁盘", "网络", "电池", "进程", "cpu", "内存", "状态"),
    "presentation": ("ppt", "powerpoint", "演示", "幻灯片", "汇报页", "slide", "slides"),
    "finance": ("股票", "a股", "行情", "走势", "财报", "akshare"),
    "danger": ("删除", "覆盖", "批量移动", "清空", "delete", "remove"),
}


@dataclass
class CapabilityMatch:
    capability: CapabilityDescriptor
    score: float
    matched_terms: list[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["capability"] = self.capability.to_dict()
        return data


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "").lower())


def _tokens(text: str) -> set[str]:
    s = str(text or "").lower()
    words = {w for w in re.findall(r"[a-zA-Z][a-zA-Z0-9_.-]+|[0-9]+", s) if len(w) > 1}
    cn = re.findall(r"[\u4e00-\u9fff]{2,}", s)
    grams: set[str] = set(words)
    for chunk in cn:
        grams.add(chunk)
        for n in (2, 3, 4):
            for i in range(0, max(0, len(chunk) - n + 1)):
                grams.add(chunk[i : i + n])
    return grams


def _expanded_query_terms(user_input: str) -> set[str]:
    compact = _compact(user_input)
    terms = _tokens(user_input)
    for canonical, aliases in _SYNONYMS.items():
        if any(_compact(alias) in compact for alias in aliases):
            terms.add(canonical)
            terms.update(_tokens(" ".join(aliases)))
    return {t for t in terms if t}


def _descriptor_terms(capability: CapabilityDescriptor) -> set[str]:
    terms = _tokens(capability.searchable_text())
    for canonical, aliases in _SYNONYMS.items():
        text = _compact(capability.searchable_text())
        if canonical in capability.actions or any(_compact(alias) in text for alias in aliases):
            terms.add(canonical)
            terms.update(_tokens(" ".join(aliases)))
    return terms


def match_capabilities(
    user_input: str,
    capabilities: list[CapabilityDescriptor],
    *,
    limit: int = 5,
) -> list[CapabilityMatch]:
    query_terms = _expanded_query_terms(user_input)
    compact_query = _compact(user_input)
    out: list[CapabilityMatch] = []
    for capability in capabilities:
        terms = _descriptor_terms(capability)
        overlap = sorted(query_terms & terms)
        if not overlap:
            # Tool id and domain direct hit still matters.
            hay = _compact(capability.searchable_text())
            direct = [x for x in query_terms if x and x in hay]
            overlap = sorted(set(direct))
        if not overlap:
            continue
        base = len(overlap) / math.sqrt(max(8, len(terms)))
        bonus = 0.0
        if capability.id.removeprefix("mcp:").lower() in compact_query:
            bonus += 0.35
        if capability.workflow_id and capability.workflow_id.lower() in compact_query:
            bonus += 0.25
        if capability.risk == "external_effect" and {"send_message", "lark"} & query_terms:
            bonus += 0.2
        if capability.domain.startswith("os_assistant") and {"app", "file", "system", "project"} & query_terms:
            bonus += 0.12
        score = min(1.0, round(base + bonus, 4))
        out.append(
            CapabilityMatch(
                capability=capability,
                score=score,
                matched_terms=overlap[:16],
                reason=f"matched {len(overlap)} semantic terms",
            )
        )
    out.sort(key=lambda item: (item.score, item.capability.source == "builtin"), reverse=True)
    return out[:limit]
