from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

try:
    from l3_node.voice_reply_plan import fallback_reply_from_plan, reply_plan_from_voice_selection
except Exception:  # pragma: no cover - voice server can still run without L3 package imports
    fallback_reply_from_plan = None
    reply_plan_from_voice_selection = None


@dataclass(frozen=True)
class LexiconEntry:
    kind: str
    canonical: str
    aliases: list[str]
    phonetic_aliases: list[str] = field(default_factory=list)


@dataclass
class EntityCandidate:
    kind: str
    canonical: str
    surface: str
    matched_text: str
    span: list[int]
    score: float
    evidence: list[str] = field(default_factory=list)
    strength: str = "weak"


@dataclass
class TaskCandidate:
    type: str
    intent: str
    slots: dict[str, str]
    corrected_text: str
    score: float
    needs_confirmation: bool
    reasons: list[str] = field(default_factory=list)
    missing_slots: list[str] = field(default_factory=list)
    uncertain_slots: list[dict[str, Any]] = field(default_factory=list)
    question: str = ""
    can_execute: bool = True
    clarification_reason: str = ""


INTENT_TRIGGER_ALIASES: dict[str, list[str]] = {
    "send_message": ["发消息", "发送消息", "发信息", "发送信息", "发条消息", "发一条消息", "消息内容", "sendmessage"],
    "open_app": ["打开", "启动", "开启", "大开", "打给", "进入", "切到", "切换到", "用一下", "open"],
    "find": ["找到", "找一下", "找", "查找", "搜索", "赵刀", "赵到", "照到", "造到", "早到", "招到", "遭到", "找刀", "看下", "看一下", "查下", "联系", "呼叫", "find"],
}

NON_TASK_EXACT_PHRASES = {"啊", "嗯", "哦", "好", "好的", "是的", "对", "没错", "谢谢", "不用了", "不要了", "不是这个"}
NON_TASK_PATTERNS = ["你说的", "你说得", "都是对的", "挺好的", "我想一下", "刚才那个", "这个方案", "不是这个意思"]
PRONOUN_OR_FILLER_SPANS = {
    "你",
    "我",
    "他",
    "她",
    "它",
    "给你",
    "给我",
    "帮我",
    "请你",
    "那个",
    "这个",
    "那个帮",
    "打开那个",
}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def normalize_for_match(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "").lower())


def ascii_words(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", str(value or "").lower())


def is_ascii_like(value: str) -> bool:
    words = ascii_words(value)
    return bool(words) and all(ord(ch) < 128 or ch.isspace() or ch in "_-" for ch in str(value or ""))


def ascii_word_sequence_contains(span_text: str, alias: str) -> bool:
    span_words = ascii_words(span_text)
    alias_words = ascii_words(alias)
    if not span_words or not alias_words:
        return False
    if len(alias_words) == 1:
        return alias_words[0] in span_words
    limit = len(span_words) - len(alias_words) + 1
    return any(span_words[idx : idx + len(alias_words)] == alias_words for idx in range(max(0, limit)))


def ascii_word_sequence_equals(span_text: str, alias: str) -> bool:
    span_words = ascii_words(span_text)
    alias_words = ascii_words(alias)
    return bool(span_words and alias_words and span_words == alias_words)


def is_ascii_text(value: str) -> bool:
    return all(ord(ch) < 128 for ch in value)


def similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def maybe_pinyin(value: str) -> str:
    try:
        from pypinyin import lazy_pinyin

        return "".join(lazy_pinyin(str(value or ""), errors="ignore")).lower()
    except Exception:
        return ""


def phonetic_fold(value: str) -> str:
    folded = normalize_for_match(value)
    return folded.replace("v", "w").replace("ph", "f").replace("ck", "k")


def load_lexicon(
    lexicon_file: Path | None = None,
    user_aliases_file: Path | None = None,
) -> list[LexiconEntry]:
    root = repo_root()
    lexicon_file = lexicon_file or root / "data" / "voice" / "domain_lexicon.json"
    user_aliases_file = user_aliases_file or root / "data" / "voice" / "user_aliases.json"

    def load_json(path: Path) -> dict[str, Any]:
        if not path.is_file():
            return {}
        return json.loads(path.read_text(encoding="utf-8-sig"))

    data = load_json(lexicon_file)
    user_aliases = load_json(user_aliases_file)
    entries: list[LexiconEntry] = []
    for kind in ("apps", "contacts", "projects"):
        for canonical, meta in (data.get(kind) or {}).items():
            if isinstance(meta, dict) and meta.get("active", True) is False:
                continue
            aliases: list[str] = []
            phonetic_aliases: list[str] = []
            if isinstance(meta, dict):
                aliases.extend(str(x) for x in meta.get("aliases", []) if str(x).strip())
                phonetic_aliases.extend(str(x) for x in meta.get("phonetic_aliases", []) if str(x).strip())
            elif isinstance(meta, list | tuple):
                aliases.extend(str(x) for x in meta if str(x).strip())
            aliases.extend(str(x) for x in (user_aliases.get(kind) or {}).get(canonical, []) if str(x).strip())
            aliases.append(str(canonical))
            entries.append(
                LexiconEntry(
                    kind=kind,
                    canonical=str(canonical),
                    aliases=list(dict.fromkeys(x.strip() for x in aliases if x.strip())),
                    phonetic_aliases=list(dict.fromkeys(x.strip() for x in phonetic_aliases if x.strip())),
                )
            )
    return entries


def extract_candidate_spans(text: str) -> list[tuple[int, int, str]]:
    spans: dict[tuple[int, int], str] = {}
    if text:
        spans[(0, len(text))] = text
    for match in re.finditer(r"[A-Za-z][A-Za-z0-9 _-]{1,}", text):
        spans[(match.start(), match.end())] = match.group(0)
    for match in re.finditer(r"[\u4e00-\u9fff]{2,}", text):
        segment = match.group(0)
        base = match.start()
        for size in range(1, min(6, len(segment)) + 1):
            for offset in range(0, len(segment) - size + 1):
                start = base + offset
                spans[(start, start + size)] = text[start : start + size]
    return [(start, end, value) for (start, end), value in sorted(spans.items())]


def score_entity_span(span_text: str, alias: str) -> tuple[float, list[str]]:
    span_norm = normalize_for_match(span_text)
    alias_norm = normalize_for_match(alias)
    evidence: list[str] = []
    if not span_norm or not alias_norm:
        return 0.0, evidence
    if span_norm == alias_norm:
        return 0.98, ["exact"]
    if span_norm in alias_norm or alias_norm in span_norm:
        if is_ascii_like(span_text) or is_ascii_like(alias):
            short_alias = len(alias_norm) <= 2
            if short_alias and not ascii_word_sequence_equals(span_text, alias):
                return 0.0, []
            if ascii_word_sequence_contains(span_text, alias) or ascii_word_sequence_contains(alias, span_text):
                return 0.92, ["substring"]
        elif min(len(span_norm), len(alias_norm)) >= 2:
            return 0.92, ["substring"]
    best = similarity(span_norm, alias_norm)
    if best >= 0.72:
        evidence.append("char_similarity")
    span_pinyin = maybe_pinyin(span_text)
    alias_pinyin = maybe_pinyin(alias) if not is_ascii_text(alias) else alias_norm
    if span_pinyin and alias_pinyin:
        pinyin_score = similarity(span_pinyin, alias_pinyin)
        best = max(best, pinyin_score)
        if pinyin_score >= 0.62:
            evidence.append("pinyin_similarity")
    folded_score = similarity(phonetic_fold(span_pinyin or span_norm), phonetic_fold(alias_pinyin or alias_norm))
    best = max(best, folded_score)
    if folded_score >= 0.64:
        evidence.append("phonetic_similarity")
    return best, evidence


def score_phonetic_alias_span(span_text: str, alias: str) -> tuple[float, list[str]]:
    score, evidence = score_entity_span(span_text, alias)
    if "exact" in evidence:
        return 0.84, ["phonetic_alias", "exact"]
    if "substring" in evidence:
        return min(score, 0.80), ["phonetic_alias", "substring"]
    if score >= 0.72:
        return min(score, 0.76), list(dict.fromkeys(["phonetic_alias", *evidence]))
    return 0.0, []


def entity_surface(entry: LexiconEntry, alias: str, matched_text: str) -> str:
    if entry.kind == "contacts":
        return entry.canonical
    if entry.kind == "apps":
        return entry.canonical
    if normalize_for_match(alias) == normalize_for_match(entry.canonical):
        return entry.canonical
    if alias in {"飞书", "薇薇安", "微微安"}:
        return alias
    if is_ascii_text(alias) and is_ascii_text(entry.canonical):
        return entry.canonical
    if matched_text and is_ascii_text(matched_text):
        return entry.canonical
    return alias


def is_pronoun_like_span(value: str) -> bool:
    normalized = normalize_for_match(value)
    return bool(normalized) and any(item in normalized for item in {normalize_for_match(x) for x in PRONOUN_OR_FILLER_SPANS})


def classify_entity_strength(kind: str, matched_text: str, score: float, evidence: list[str]) -> str:
    matched_norm = normalize_for_match(matched_text)
    if "phonetic_alias" in evidence:
        if kind == "contacts" and score >= 0.78 and len(matched_norm) >= 1:
            return "medium"
        if kind == "projects" and score >= 0.80 and len(matched_norm) >= 2:
            return "medium"
        return "weak"
    if "exact" in evidence or "substring" in evidence:
        return "strong"
    if kind == "contacts":
        if is_pronoun_like_span(matched_text) or len(matched_norm) <= 1:
            return "weak"
        if score >= 0.82 and len(matched_norm) >= 2:
            return "medium"
        return "weak"
    if score >= 0.86 and len(matched_norm) >= 2:
        return "strong"
    if score >= 0.72 and len(matched_norm) >= 2:
        return "medium"
    return "weak"


def global_entity_scan(text: str, entries: list[LexiconEntry]) -> list[EntityCandidate]:
    best_by_entity: dict[tuple[str, str], EntityCandidate] = {}
    for start, end, span_text in extract_candidate_spans(text):
        span_norm = normalize_for_match(span_text)
        span_is_short_ascii_fragment = is_ascii_like(span_text) and len(span_norm) <= 2
        for entry in entries:
            alias_items = [(entry.canonical, False), *[(alias, False) for alias in entry.aliases], *[(alias, True) for alias in entry.phonetic_aliases]]
            for alias, is_phonetic_alias in alias_items:
                score, evidence = score_phonetic_alias_span(span_text, alias) if is_phonetic_alias else score_entity_span(span_text, alias)
                if score < 0.62:
                    continue
                if len(span_norm) <= 1 and "phonetic_alias" not in evidence:
                    continue
                if span_is_short_ascii_fragment and "exact" not in evidence:
                    continue
                if entry.kind == "projects" and score < 0.72:
                    continue
                if entry.kind == "apps" and not any(x in evidence for x in ("exact", "substring")) and score < 0.86:
                    continue
                if entry.kind == "apps" and "phonetic_alias" in evidence and score < 0.88:
                    continue
                if not evidence:
                    evidence = ["fuzzy_similarity"]
                candidate = EntityCandidate(
                    kind=entry.kind,
                    canonical=entry.canonical,
                    surface=entity_surface(entry, alias, span_text),
                    matched_text=span_text,
                    span=[start, end],
                    score=round(min(score, 0.98), 3),
                    evidence=list(dict.fromkeys(["global_fuzzy_scan", *evidence])),
                    strength=classify_entity_strength(entry.kind, span_text, round(min(score, 0.98), 3), list(dict.fromkeys(["global_fuzzy_scan", *evidence]))),
                )
                key = (entry.kind, entry.canonical)
                current = best_by_entity.get(key)
                candidate_span_len = max(0, candidate.span[1] - candidate.span[0])
                current_span_len = max(0, current.span[1] - current.span[0]) if current is not None else -1
                if current is None or candidate.score > current.score or (candidate.score == current.score and candidate_span_len > current_span_len):
                    best_by_entity[key] = candidate
    return sorted(best_by_entity.values(), key=lambda item: item.score, reverse=True)[:10]


def exact_action_score(text: str, aliases: list[str]) -> float:
    text_norm = normalize_for_match(text)
    return 1.0 if any(normalize_for_match(alias) in text_norm for alias in aliases if normalize_for_match(alias)) else 0.0


def best_window_match(text: str, target: str) -> float:
    text_norm = normalize_for_match(text)
    target_norm = normalize_for_match(target)
    if not text_norm or not target_norm:
        return 0.0
    if target_norm in text_norm:
        return 1.0
    best = 0.0
    for extra in range(0, 3):
        size = len(target_norm) + extra
        if size > len(text_norm):
            continue
        for idx in range(0, len(text_norm) - size + 1):
            best = max(best, similarity(text_norm[idx : idx + size], target_norm))
    return best


def weak_action_score(text: str, aliases: list[str]) -> float:
    best = 0.0
    text_pinyin = maybe_pinyin(text)
    for alias in aliases:
        best = max(best, best_window_match(text, alias))
        alias_pinyin = maybe_pinyin(alias)
        if alias_pinyin and text_pinyin:
            best = max(best, best_window_match(text_pinyin, alias_pinyin))
    return round(best, 3)


def is_strong_entity(entity: EntityCandidate) -> bool:
    return entity.strength == "strong"


def is_medium_or_strong_entity(entity: EntityCandidate) -> bool:
    return entity.strength in {"medium", "strong"}


def classify_utterance(text: str, entities: list[EntityCandidate], explicit_action_scores: dict[str, float]) -> dict[str, Any]:
    normalized = normalize_for_match(text)
    has_explicit_action = any(v > 0 for v in explicit_action_scores.values())
    has_strong_entity = any(is_strong_entity(e) for e in entities)
    has_weak_entity = bool(entities) and not has_strong_entity
    evidence = {
        "has_explicit_action": has_explicit_action,
        "has_strong_entity": has_strong_entity,
        "has_weak_entity": has_weak_entity,
        "entity_count": len(entities),
        "top_entity_score": max((e.score for e in entities), default=0.0),
    }
    if not normalized:
        return {"type": "non_task_audio", "task_likelihood": 0.0, "reasons": ["empty_text"], "evidence": evidence}
    if normalized in {normalize_for_match(x) for x in NON_TASK_EXACT_PHRASES}:
        return {"type": "non_task_audio" if len(normalized) <= 1 else "chat_or_statement", "task_likelihood": 0.02, "reasons": ["known_non_task_phrase"], "evidence": evidence}
    reasons: list[str] = []
    likelihood = 0.08
    if has_explicit_action:
        likelihood += 0.48
        reasons.append("explicit_action")
    if has_strong_entity:
        likelihood += 0.30
        reasons.append("strong_entity")
    elif has_weak_entity:
        likelihood += 0.08
        reasons.append("weak_entity")
    if any(normalize_for_match(p) in normalized for p in NON_TASK_PATTERNS):
        likelihood -= 0.45
        reasons.append("statement_like_text")
    if has_weak_entity and not has_explicit_action and not has_strong_entity:
        likelihood -= 0.12
        reasons.append("weak_entity_only")
    likelihood = round(max(0.0, min(0.99, likelihood)), 3)
    return {"type": "task_request" if likelihood >= 0.55 else "chat_or_statement", "task_likelihood": likelihood, "reasons": list(dict.fromkeys(reasons)), "evidence": evidence}


def slot_key(kind: str) -> str:
    return {"apps": "app", "contacts": "contact", "projects": "project"}.get(kind, kind.rstrip("s"))


def replace_entity_spans(text: str, entities: list[EntityCandidate]) -> str:
    corrected = text
    for entity in sorted(entities, key=lambda item: item.span[0], reverse=True):
        start, end = entity.span
        if 0 <= start <= end <= len(corrected):
            corrected = corrected[:start] + entity.surface + corrected[end:]
    return corrected


def task_text(text: str, intent: str, entities: list[EntityCandidate]) -> str:
    if not entities:
        return text
    primary = entities[0]
    if intent == "open_app":
        return f"打开{primary.surface}"
    if intent in {"find_app", "find_contact"}:
        return f"找到{primary.surface}"
    if intent == "contact_interaction":
        return f"打开{primary.surface}会话"
    return replace_entity_spans(text, entities)


def make_task(text: str, intent: str, entities: list[EntityCandidate], score: float, reasons: list[str]) -> TaskCandidate:
    fuzzy_entity = any(not is_strong_entity(entity) for entity in entities)
    return TaskCandidate(
        type="task_requires_confirmation" if (score < 0.82 or intent in {"send_message", "contact_interaction"} or fuzzy_entity) else "task_ready",
        intent=intent,
        slots={slot_key(entity.kind): entity.canonical for entity in entities},
        corrected_text=task_text(text, intent, entities),
        score=round(min(score, 0.99), 3),
        needs_confirmation=score < 0.82 or intent in {"send_message", "contact_interaction"} or fuzzy_entity,
        reasons=list(dict.fromkeys(reasons)),
    )


def no_task_candidate(text: str, utterance: dict[str, Any]) -> TaskCandidate:
    return TaskCandidate(
        type="no_task",
        intent="no_task",
        slots={},
        corrected_text=text,
        score=round(max(0.01, min(0.99, 1.0 - float(utterance.get("task_likelihood") or 0.0))), 3),
        needs_confirmation=False,
        reasons=[str(x) for x in utterance.get("reasons", [])] or ["no_task_competition"],
        can_execute=False,
    )


def message_content_after_marker(text: str) -> str:
    for marker in ("\u5185\u5bb9\u662f", "\u6d88\u606f\u662f", "\u6b63\u6587\u662f", "\u8bf4\u7684\u662f", "message is", "content is"):
        idx = normalize_for_match(text).find(normalize_for_match(marker))
        if idx >= 0:
            raw_idx = text.find(marker)
            if raw_idx >= 0:
                return text[raw_idx + len(marker) :].strip()
    return ""


def has_send_surface(text: str) -> bool:
    normalized = normalize_for_match(text)
    return any(
        marker in normalized
        for marker in (
            "发",
            "消息",
            "信息",
            "告诉",
            "通知",
            "send",
            "message",
            "content",
        )
    )


def make_clarification(
    text: str,
    *,
    intent: str,
    known_entities: list[EntityCandidate],
    missing_slots: list[str],
    uncertain_entities: list[EntityCandidate],
    question: str,
    score: float,
    reason: str,
) -> TaskCandidate:
    return TaskCandidate(
        type="clarification_required",
        intent=intent,
        slots={slot_key(entity.kind): entity.canonical for entity in known_entities if is_medium_or_strong_entity(entity)},
        corrected_text=replace_entity_spans(text, [entity for entity in known_entities if is_medium_or_strong_entity(entity)]),
        score=round(max(0.01, min(score, 0.99)), 3),
        needs_confirmation=False,
        reasons=[reason],
        missing_slots=list(dict.fromkeys(missing_slots)),
        uncertain_slots=[
            {
                "slot": slot_key(entity.kind),
                "value": entity.canonical,
                "score": entity.score,
                "strength": entity.strength,
                "matched_text": entity.matched_text,
                "evidence": entity.evidence,
            }
            for entity in uncertain_entities[:5]
        ],
        question=question,
        can_execute=False,
        clarification_reason=reason,
    )


def send_message_question(app: EntityCandidate | None, missing_slots: list[str], uncertain_contacts: list[EntityCandidate]) -> str:
    app_name = app.canonical if app and is_medium_or_strong_entity(app) else ""
    if "contact" in missing_slots:
        strong_options = [c.canonical for c in uncertain_contacts if c.strength == "medium"][:3]
        if strong_options:
            prefix = f"在 {app_name} " if app_name else ""
            return f"你要{prefix}发给 {', '.join(strong_options)}，还是其他人？"
        prefix = f"在 {app_name} " if app_name else ""
        return f"我听到你想{prefix}发消息。你要发给谁？"
    if "message_content" in missing_slots:
        return f"要发送的内容是什么？"
    return "这条语音我没有完全听清，请再说一遍要发给谁和内容。"


def generate_task_candidates(text: str, entities: list[EntityCandidate], utterance: dict[str, Any], explicit: dict[str, float]) -> list[TaskCandidate]:
    normalized = normalize_for_match(text)
    is_short = len(normalized) <= 12
    action = {
        "open": weak_action_score(text, INTENT_TRIGGER_ALIASES["open_app"]),
        "find": weak_action_score(text, INTENT_TRIGGER_ALIASES["find"]),
        "send": weak_action_score(text, INTENT_TRIGGER_ALIASES["send_message"] + ["内容是", "告诉", "通知"]),
    }
    by_kind_all = {
        "apps": [e for e in entities if e.kind == "apps"],
        "contacts": [e for e in entities if e.kind == "contacts"],
        "projects": [e for e in entities if e.kind == "projects"],
    }
    by_kind = {
        "apps": [e for e in by_kind_all["apps"] if is_medium_or_strong_entity(e)],
        "contacts": [
            e
            for e in by_kind_all["contacts"]
            if is_medium_or_strong_entity(e) or (e.strength == "weak" and is_ascii_like(e.matched_text) and len(normalize_for_match(e.matched_text)) >= 4)
        ],
        "projects": [e for e in by_kind_all["projects"] if is_medium_or_strong_entity(e)],
    }
    tasks: list[TaskCandidate] = [no_task_candidate(text, utterance)]
    has_explicit_action = any(v > 0 for v in explicit.values())
    has_action_signal = has_explicit_action or max(action.values(), default=0.0) >= 0.45 or has_send_surface(text)
    top_entity_score = max((e.score for e in entities), default=0.0)
    if not has_action_signal and not (len(normalized) <= 10 and top_entity_score >= 0.86):
        return sorted(tasks, key=lambda item: item.score, reverse=True)
    if entities and all(not is_strong_entity(e) for e in entities) and not has_action_signal:
        return sorted(tasks, key=lambda item: item.score, reverse=True)

    send_action_present = has_send_surface(text)
    message_content = message_content_after_marker(text)
    give_marker_present = "\u7ed9" in text or "\u7d66" in text
    implicit_app_contact_message = give_marker_present and bool(by_kind["apps"]) and bool(by_kind["contacts"])
    send_context = (send_action_present and (bool(by_kind["contacts"]) or bool(by_kind["apps"]) or give_marker_present)) or implicit_app_contact_message

    if send_context:
        app = by_kind["apps"][0] if by_kind["apps"] else None
        strong_contacts = [e for e in by_kind_all["contacts"] if is_strong_entity(e)]
        medium_contacts = [e for e in by_kind_all["contacts"] if e.strength == "medium"]
        weak_contacts = [e for e in by_kind_all["contacts"] if e.strength == "weak"]
        medium_contact_in_slot = bool(medium_contacts) and (give_marker_present or app is not None)
        usable_contact = strong_contacts[0] if strong_contacts else (medium_contacts[0] if (medium_contacts and (message_content or medium_contact_in_slot)) else None)
        missing_slots: list[str] = []
        if usable_contact is None:
            missing_slots.append("contact")
        if not message_content:
            missing_slots.append("message_content")
        if missing_slots:
            known_entities = [entity for entity in [app, usable_contact] if entity is not None]
            uncertain_entities = ([] if usable_contact else [*medium_contacts, *weak_contacts]) + ([] if app and is_medium_or_strong_entity(app) else ([app] if app else []))
            tasks.append(
                make_clarification(
                    text,
                    intent="send_message",
                    known_entities=known_entities,
                    missing_slots=missing_slots,
                    uncertain_entities=uncertain_entities,
                    question=send_message_question(app, missing_slots, [*medium_contacts, *weak_contacts]),
                    score=0.72 + (0.08 if app and is_medium_or_strong_entity(app) else 0.0),
                    reason="send_message_missing_or_weak_required_slot",
                )
            )
            return sorted(tasks, key=lambda item: item.score, reverse=True)[:8]

    if by_kind["apps"]:
        app = by_kind["apps"][0]
        open_score = 0.34 + app.score * 0.42 + action["open"] * 0.18 + (0.06 if is_short else 0.0)
        if action["find"] > action["open"] + 0.1:
            open_score -= 0.08
        if send_context:
            open_score -= 0.15
        tasks.append(make_task(text, "open_app", [app], open_score, ["app_entity_anchor", "open_template"]))
        if action["find"] >= 0.45 and not send_context:
            tasks.append(make_task(text, "find_app", [app], 0.32 + app.score * 0.42 + action["find"] * 0.24, ["app_entity_anchor", "find_like_action"]))
    if by_kind["contacts"]:
        contact = by_kind["contacts"][0]
        tasks.append(make_task(text, "find_contact", [contact], 0.32 + contact.score * 0.40 + action["find"] * 0.18 + (0.08 if is_short else 0.0), ["contact_entity_anchor", "find_contact_template"]))
        interaction_score = 0.30 + contact.score * 0.36 + max(action["open"], action["find"]) * 0.24 + (0.06 if "的" in text else 0.0)
        tasks.append(make_task(text, "contact_interaction", [contact], interaction_score, ["contact_entity_anchor", "contact_interaction_template"]))
        if send_context:
            send_entities = ([by_kind["apps"][0]] if by_kind["apps"] else []) + [contact]
            send_score = 0.36 + contact.score * 0.34 + action["send"] * 0.30 + ((by_kind["apps"][0].score * 0.10) if by_kind["apps"] else 0.0)
            tasks.append(make_task(text, "send_message", send_entities, send_score, ["contact_entity_anchor", "send_template"]))
    if by_kind["projects"]:
        project = by_kind["projects"][0]
        tasks.append(make_task(text, "open_project", [project], 0.30 + project.score * 0.40 + max(action["open"], action["find"]) * 0.18, ["project_entity_anchor"]))
    return sorted(tasks, key=lambda item: item.score, reverse=True)[:8]


def understand_voice_text(text: str, entries: list[LexiconEntry] | None = None) -> dict[str, Any]:
    entries = entries if entries is not None else load_lexicon()
    entities = global_entity_scan(text, entries)
    explicit = {
        "open": exact_action_score(text, INTENT_TRIGGER_ALIASES["open_app"]),
        "find": exact_action_score(text, INTENT_TRIGGER_ALIASES["find"]),
        "send": exact_action_score(text, INTENT_TRIGGER_ALIASES["send_message"] + ["内容是", "告诉", "通知"]),
    }
    utterance = classify_utterance(text, entities, explicit)
    tasks = generate_task_candidates(text, entities, utterance, explicit)
    selected = tasks[0] if tasks else None
    selected_dict = asdict(selected) if selected else {}
    reply_plan = {}
    reply_fallback = ""
    if reply_plan_from_voice_selection is not None and selected_dict:
        try:
            plan = reply_plan_from_voice_selection(
                selected=selected_dict,
                raw_text=text,
                corrected_text=str(selected_dict.get("corrected_text") or text),
            )
            if plan is not None:
                reply_plan = plan.to_dict()
                if fallback_reply_from_plan is not None:
                    reply_fallback = fallback_reply_from_plan(plan)
        except Exception:
            reply_plan = {}
            reply_fallback = ""
    return {
        "strategy": "global_entity_first",
        "asr_texts": [{"engine": "jvs", "text": text}],
        "utterance": utterance,
        "entity_candidates": [asdict(item) for item in entities],
        "task_candidates": [asdict(item) for item in tasks],
        "selected": selected_dict,
        "reply_plan": reply_plan,
        "reply_source": "reply_plan" if reply_plan else "none",
        "user_message": reply_fallback or user_message_for_selection(selected_dict, text),
        "user_message_source": "fallback_template" if reply_fallback else "legacy_template",
    }


def user_message_for_selection(selected: dict[str, Any], raw_text: str) -> str:
    selected_type = str(selected.get("type") or "")
    if selected_type == "clarification_required":
        return str(selected.get("question") or "这句我没有完全听清，请再说一遍。").strip()
    if selected_type == "task_requires_confirmation":
        corrected = str(selected.get("corrected_text") or raw_text).strip()
        if corrected:
            return f"我理解为：{corrected}。执行前需要你确认。"
        return "这个操作需要你确认后再执行。"
    if selected_type == "task_ready":
        corrected = str(selected.get("corrected_text") or raw_text).strip()
        return corrected
    return ""


class VoiceUnderstandingCorrector:
    def __init__(self, lexicon_file: Path | None = None, user_aliases_file: Path | None = None) -> None:
        self.lexicon_file = lexicon_file
        self.user_aliases_file = user_aliases_file
        self._entries: list[LexiconEntry] | None = None

    def _load_entries(self) -> list[LexiconEntry]:
        if self._entries is None:
            self._entries = load_lexicon(self.lexicon_file, self.user_aliases_file)
        return self._entries

    def correct(self, text: str) -> dict[str, Any]:
        entities = global_entity_scan(text, self._load_entries())
        correction_entities = [entity for entity in entities if is_medium_or_strong_entity(entity)]
        corrected = replace_entity_spans(text, correction_entities) if correction_entities else text
        confidence = max((entity.score for entity in correction_entities), default=0.0)
        entity_only_understanding = {
            "strategy": "stt_entity_correction_only",
            "asr_texts": [{"engine": "jvs", "text": text}],
            "entity_candidates": [asdict(item) for item in entities],
            "task_candidates": [],
            "selected": {},
            "reply_plan": {},
            "reply_source": "none",
            "voice_layer_scope": "stt_only",
            "note": "Voice layer only performs STT/entity correction/hotword support; L3 owns intent, slot filling, clarification, and task decisions.",
        }
        return {
            "raw_text": text,
            "corrected_text": corrected,
            "user_message": "",
            "user_message_source": "",
            "confidence": round(confidence, 3),
            "needs_confirmation": False,
            "understanding": entity_only_understanding,
            "reply_plan": {},
        }
