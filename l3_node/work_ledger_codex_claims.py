"""Evidence-grounded claim fusion for Work Ledger Codex consultations."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable


_NEGATIVE_MARKERS = (
    "未完成",
    "尚未",
    "没有",
    "失败",
    "未通过",
    "阻塞",
    "待验证",
    "待确认",
    "not completed",
    "not implemented",
    "failed",
    "failure",
    "blocked",
    "pending",
    "not verified",
)
_POSITIVE_MARKERS = (
    "已完成",
    "完成了",
    "已实现",
    "已修复",
    "测试通过",
    "验证通过",
    "已交付",
    "completed",
    "implemented",
    "fixed",
    "tests passed",
    "verified",
    "delivered",
)
_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "this",
    "that",
    "into",
    "当前",
    "项目",
    "工作",
    "已经",
    "进行",
    "一个",
    "需要",
    "相关",
    "可以",
    "通过",
}


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


def _has_negative_marker(value: str) -> bool:
    lower = str(value or "").casefold()
    return any(marker in lower for marker in _NEGATIVE_MARKERS)


def _has_positive_marker(value: str) -> bool:
    lower = str(value or "").casefold()
    return any(marker in lower for marker in _POSITIVE_MARKERS) or bool(
        re.search(r"已经.{0,12}(?:完成|实现|修复|交付|发布|发送|通过)", lower)
    )


def _claim_id(invocation_id: str, index: int, text: str) -> str:
    digest = hashlib.sha256(
        f"{invocation_id}:{index}:{_compact(text)}".encode("utf-8")
    ).hexdigest()[:16]
    return f"codex-claim-{digest}"


def _clean_claim_line(value: str) -> str:
    line = re.sub(r"\s+", " ", str(value or "")).strip()
    line = re.sub(r"^\s*(?:[-*•]+\s*|\d{1,3}[.)、]\s*)", "", line)
    line = re.sub(r"^#{1,6}\s*", "", line)
    return line.strip()


def extract_codex_claims(
    answer: str,
    *,
    invocation_id: str = "",
    max_claims: int = 80,
) -> list[dict[str, Any]]:
    """Split a Codex answer into stable, independently auditable claims."""

    candidates: list[str] = []
    for raw in str(answer or "").splitlines():
        line = _clean_claim_line(raw)
        if not line:
            continue
        if len(line) <= 28 and line.endswith((":", "：")):
            continue
        parts = (
            re.split(r"(?<=[。！？!?；;])\s+", line)
            if len(line) > 320
            else [line]
        )
        candidates.extend(part.strip() for part in parts if part.strip())

    claims: list[dict[str, Any]] = []
    seen: set[str] = set()
    for text in candidates:
        normalized = _compact(text)
        if len(normalized) < 6 or normalized in seen:
            continue
        seen.add(normalized)
        claim_type = classify_codex_claim(text)
        claims.append(
            {
                "claim_id": _claim_id(invocation_id, len(claims), text),
                "text": text[:1200],
                "claim_type": claim_type,
                "assertion_strength": (
                    "strong"
                    if claim_type in {"completion", "verification", "delivery"}
                    else "normal"
                ),
            }
        )
        if len(claims) >= max(1, min(int(max_claims or 80), 200)):
            break
    return claims


def classify_codex_claim(text: str) -> str:
    value = str(text or "")
    lower = value.casefold()
    if re.search(r"(下一步|建议|后续|应该|需要补|recommend|next step)", lower):
        return "recommendation"
    if re.search(r"(风险|阻塞|问题|失败|未完成|不确定|risk|blocked|failure)", lower):
        return "risk"
    if re.search(r"(测试通过|验证通过|测试失败|未测试|未验证|test|verify|ci\b)", lower):
        return "verification"
    if re.search(r"(已交付|已发布|已发送|delivered|released|deployed)", lower):
        return "delivery"
    if re.search(
        r"(已完成|完成了|已实现|已修复|新增了|已经.{0,12}(?:完成|实现|修复)|implemented|completed|fixed)",
        lower,
    ):
        return "completion"
    if re.search(r"([A-Za-z0-9_.-]+\.(?:py|ts|tsx|rs|md|json|yaml|yml)|[/\\][A-Za-z0-9_.\\/-]+)", value):
        return "file_change"
    if re.search(r"(决定|选择|采用|取舍|decision|selected|chose)", lower):
        return "decision"
    if re.search(r"(可能|推测|猜测|似乎|maybe|probably|appears)", lower):
        return "hypothesis"
    return "interpretation"


def _path_tokens(text: str) -> set[str]:
    return {
        token.replace("\\", "/").casefold().strip("`'\".,;:()[]{}")
        for token in re.findall(
            r"(?:[A-Za-z]:[\\/])?[A-Za-z0-9_.-]+(?:[\\/][A-Za-z0-9_.-]+)+|"
            r"[A-Za-z0-9_.-]+\.(?:py|ts|tsx|js|jsx|rs|md|json|ya?ml|toml|ps1)",
            str(text or ""),
        )
        if token
    }


def _semantic_tokens(text: str) -> set[str]:
    value = str(text or "").casefold()
    tokens = {
        token
        for token in re.findall(r"[a-z][a-z0-9_.-]{2,}", value)
        if token not in _STOPWORDS
    }
    for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", value):
        if chunk in _STOPWORDS:
            continue
        if len(chunk) <= 4:
            tokens.add(chunk)
        else:
            tokens.update(chunk[index : index + 2] for index in range(len(chunk) - 1))
    return tokens


def _safe_json(value: Any, max_chars: int = 10000) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        text = str(value)
    return text[:max_chars]


def _evidence_catalog(evidence: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in evidence:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "unknown")
        if source == "codex_work_plan_consultation":
            continue
        summary = str(item.get("summary") or "").strip()
        payload_text = _safe_json(item.get("payload") or {})
        text = f"{summary}\n{payload_text}".strip()
        if not text:
            continue
        trust_level = str(item.get("trust_level") or "system_observed")
        source_lower = source.casefold()
        if trust_level == "user_confirmed":
            strength = 1.0
        elif any(
            token in source_lower
            for token in ("verification", "outcome", "test_result", "value_event")
        ):
            strength = 0.95
        elif any(
            token in source_lower
            for token in ("git", "file", "checkpoint", "runtime")
        ):
            strength = 0.72
        else:
            strength = 0.5
        rows.append(
            {
                "evidence_id": str(item.get("evidence_id") or ""),
                "source": source,
                "summary": summary[:300],
                "trust_level": trust_level,
                "strength": strength,
                "text": text,
                "paths": _path_tokens(text),
                "tokens": _semantic_tokens(text),
                "negative": _has_negative_marker(text),
                "positive": _has_positive_marker(text),
            }
        )
    return rows


def _match_score(claim: dict[str, Any], evidence: dict[str, Any]) -> float:
    text = str(claim.get("text") or "")
    claim_paths = _path_tokens(text)
    claim_tokens = _semantic_tokens(text)
    evidence_paths = set(evidence.get("paths") or set())
    evidence_tokens = set(evidence.get("tokens") or set())
    path_overlap = claim_paths & evidence_paths
    token_overlap = claim_tokens & evidence_tokens
    token_score = len(token_overlap) / max(1, min(len(claim_tokens), 12))
    score = min(1.0, token_score * 0.65 + (0.7 if path_overlap else 0.0))
    return round(score * float(evidence.get("strength") or 0.5), 4)


def _reference(row: dict[str, Any], score: float) -> dict[str, Any]:
    return {
        "evidence_id": row.get("evidence_id"),
        "source": row.get("source"),
        "summary": row.get("summary"),
        "trust_level": row.get("trust_level"),
        "match_score": score,
    }


def _fuse_claim(
    claim: dict[str, Any],
    catalog: list[dict[str, Any]],
) -> dict[str, Any]:
    claim_text = str(claim.get("text") or "")
    claim_lower = claim_text.casefold()
    claim_positive = _has_positive_marker(claim_lower)
    claim_negative = _has_negative_marker(claim_lower)
    matches: list[tuple[float, dict[str, Any]]] = []
    counters: list[tuple[float, dict[str, Any]]] = []
    for row in catalog:
        score = _match_score(claim, row)
        if score < 0.16:
            continue
        polarity_conflict = (
            (claim_positive and row.get("negative"))
            or (claim_negative and row.get("positive"))
        )
        if polarity_conflict:
            counters.append((score, row))
        else:
            matches.append((score, row))
    matches.sort(key=lambda item: item[0], reverse=True)
    counters.sort(key=lambda item: item[0], reverse=True)
    support = [_reference(row, score) for score, row in matches[:6]]
    counter = [_reference(row, score) for score, row in counters[:4]]
    claim_type = str(claim.get("claim_type") or "interpretation")
    strong_support = any(
        float(row.get("strength") or 0) >= 0.9 for _, row in matches
    )
    file_support = any(
        any(
            token in str(row.get("source") or "").casefold()
            for token in ("git", "file", "checkpoint", "runtime")
        )
        for _, row in matches
    )

    unknown_reasons: list[str] = []
    can_support_completion = False
    if counter:
        disposition = "rejected_conflict"
        unknown_reasons.append("counter_evidence_found")
    elif claim_type == "recommendation":
        disposition = "recommendation"
    elif not support:
        disposition = "unknown_requires_confirmation"
        unknown_reasons.append("no_matching_non_codex_evidence")
    elif claim_type in {"verification", "delivery", "completion"}:
        if strong_support:
            disposition = "accepted_fact"
            can_support_completion = True
        else:
            disposition = "supported_interpretation"
            unknown_reasons.append("completion_requires_user_or_verified_evidence")
    elif claim_type == "file_change" and file_support:
        disposition = "accepted_fact"
    elif claim_type == "decision":
        if strong_support:
            disposition = "accepted_fact"
        else:
            disposition = "supported_interpretation"
            unknown_reasons.append("decision_not_user_confirmed")
    elif claim_type == "hypothesis":
        disposition = "unknown_requires_confirmation"
        unknown_reasons.append("hypothesis_requires_confirmation")
    else:
        disposition = "supported_interpretation"

    allowed_uses = {
        "accepted_fact": ["context", "progress", "risk", "next_step"],
        "supported_interpretation": ["context", "risk", "next_step"],
        "recommendation": ["next_step"],
        "unknown_requires_confirmation": ["confirmation_queue"],
        "rejected_conflict": ["conflict_log"],
    }[disposition]
    return {
        **claim,
        "disposition": disposition,
        "supporting_evidence": support,
        "counter_evidence": counter,
        "unknown_reasons": unknown_reasons,
        "allowed_uses": allowed_uses,
        "can_support_completion": can_support_completion,
    }


def build_codex_claim_fusion(
    answer: str,
    evidence: Iterable[dict[str, Any]],
    *,
    invocation_id: str = "",
    prompt_hash: str = "",
) -> dict[str, Any]:
    claims = extract_codex_claims(answer, invocation_id=invocation_id)
    catalog = _evidence_catalog(evidence)
    fused = [_fuse_claim(claim, catalog) for claim in claims]
    disposition_counts: dict[str, int] = {}
    for claim in fused:
        key = str(claim.get("disposition") or "unknown")
        disposition_counts[key] = disposition_counts.get(key, 0) + 1
    return {
        "schema_version": 1,
        "invocation_id": invocation_id,
        "prompt_hash": prompt_hash,
        "claim_count": len(fused),
        "evidence_catalog_count": len(catalog),
        "disposition_counts": disposition_counts,
        "accepted_claim_ids": [
            claim["claim_id"]
            for claim in fused
            if claim.get("disposition") == "accepted_fact"
        ],
        "confirmation_queue": [
            {
                "claim_id": claim["claim_id"],
                "text": claim["text"],
                "reasons": claim.get("unknown_reasons") or [],
            }
            for claim in fused
            if claim.get("disposition") == "unknown_requires_confirmation"
        ],
        "conflicts": [
            {
                "claim_id": claim["claim_id"],
                "text": claim["text"],
                "counter_evidence": claim.get("counter_evidence") or [],
            }
            for claim in fused
            if claim.get("disposition") == "rejected_conflict"
        ],
        "claims": fused,
    }
