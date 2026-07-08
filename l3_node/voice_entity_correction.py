"""Voice STT entity canonicalization for mission routing.

This layer sits between noisy speech transcripts and deterministic routing. It
edits only entity-like slots (app/contact/project), treats message bodies as
read-only text, and exposes suspect tokens for downstream risk gates.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Literal

EntityKind = Literal["app", "contact", "project"]


@dataclass(frozen=True)
class LexiconEntity:
    kind: EntityKind
    canonical: str
    aliases: tuple[str, ...]
    source: str = "builtin"
    active: bool = True


@dataclass
class EntityCorrection:
    kind: EntityKind
    original: str
    canonical: str
    start: int
    end: int
    reason: str
    confidence: float


@dataclass
class SuspectToken:
    token: str
    kind: EntityKind
    candidates: list[str]
    reason: str
    confidence: float
    start: int = 0
    end: int = 0


@dataclass
class VoiceCorrectionResult:
    raw_text: str
    cleaned_text: str
    corrected_text: str
    corrections: list[EntityCorrection] = field(default_factory=list)
    suspect_tokens: list[SuspectToken] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.corrected_text != self.raw_text or bool(self.corrections)


_BUILTIN_ENTITIES: tuple[LexiconEntity, ...] = (
    LexiconEntity("app", "Lark", ("lark", "feishu", "flybook", "\u98de\u4e66", "luck", "lock", "\u62c9\u514b", "\u62c9")),
    LexiconEntity("app", "Chrome", ("chrome", "google chrome", "clone", "\u6d4f\u89c8\u5668")),
    LexiconEntity("app", "VS Code", ("vs code", "vscode", "visual studio code", "ws code", "w s code")),
    LexiconEntity("app", "Codex", ("codex", "code x", "\u6263\u5f97\u514b\u65af")),
    LexiconEntity("contact", "Vivian", ("vivian", "vivi", "viian", "vivan", "vivien", "\u8587\u8587\u5b89", "\u5fae\u5fae\u5b89", "v\u8587m", "v\u8587 m", "v \u8587 m", "V\u8587")),
    LexiconEntity("contact", "Neil", ("neil", "neal", "niel")),
    LexiconEntity("contact", "Ethan", ("ethan", "eason", "e than")),
    LexiconEntity("project", "Jachin", ("jachin", "jacking", "\u52a0\u52e4", "\u5609\u94a6")),
)

_KIND_SECTIONS: dict[str, EntityKind] = {"apps": "app", "contacts": "contact", "projects": "project"}
_APP_CONTEXT_RE = re.compile(r"(?:\u6253\u5f00|\u542f\u52a8|\u5207\u6362\u5230|\u805a\u7126|\u8fd0\u884c|open|launch|start|switch\s+to)\s*([A-Za-z][A-Za-z\s.-]{0,32}|[\u4e00-\u9fffA-Za-z\s.-]{1,32})", re.I)
_CONTACT_CONTEXT_RE = re.compile(
    r"(?:\u7ed9|\u5411|\u53d1\u7ed9|\u53d1\u9001\u7ed9|\u53d1\u5230|\u53d1\u9001\u5230|to)\s*([A-Za-z\u4e00-\u9fff][A-Za-z\u4e00-\u9fff\s.-]{0,40})\s*(?:\u53d1|\u53d1\u9001|\u8bf4|\u544a\u8bc9|message|send)?",
    re.I,
)
_PROJECT_CONTEXT_RE = re.compile(
    r"(?:\u603b\u7ed3|\u5206\u6790|\u770b\u770b|\u67e5\u770b|\u6574\u7406|\u68b3\u7406)\s*([A-Za-z][A-Za-z0-9_.-]{1,80}|[\u4e00-\u9fffA-Za-z0-9_.-]{2,80})\s*(?:\u6700\u8fd1|\u9879\u76ee|\u7684|\u8fd9\u51e0\u5929|\u8fd9\u4e24\u5929)?",
    re.I,
)
_MESSAGE_START_RE = re.compile(r"(?:\u5185\u5bb9\u662f|\u6d88\u606f\u662f|\u6b63\u6587\u662f|\u8bf4\u7684\u662f|message\s+is|content\s+is)\s*", re.I)
_NOISE_LEADING_RE = re.compile(r"^(?:\u90a3\u4e2a|\u55ef|\u5443|\u989d|\u554a|\u5c31\u662f|\u8bf7\u4f60|\u9ebb\u70e6\u4f60)\s*(?:\u90a3\u4e2a|\u55ef|\u5443|\u989d|\u554a|\u5c31\u662f)?\s*")
_EMOJI_TAIL_RE = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]+$")
_SPLIT_CUT_RE = re.compile(r"(?:\u7136\u540e|\u4e4b\u540e|\u518d|\u5e76\u4e14|\u5e76|\u7ed9|\u5411|\u5185\u5bb9\u662f|\u6d88\u606f\u662f|\u6b63\u6587\u662f|message|content|\u53d1|\u53d1\u9001|\u8bf4|\u544a\u8bc9)", re.I)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _lexicon_paths() -> list[Path]:
    root = _repo_root()
    return [
        root / "data" / "voice" / "domain_lexicon.json",
        root / "data" / "voice" / "user_aliases.json",
        root / "config" / "voice_domain_lexicon.json",
    ]


def _clean_word(raw: Any) -> str:
    word = re.sub(r"\s+", " ", str(raw or "").strip())
    return word[:80]


def _entity_from_value(kind: EntityKind, canonical: str, value: Any, source: str) -> LexiconEntity | None:
    name = _clean_word(canonical)
    if not name:
        return None
    aliases: list[str] = []
    active = True
    if isinstance(value, dict):
        name = _clean_word(value.get("canonical") or value.get("name") or canonical)
        aliases = [_clean_word(x) for x in value.get("aliases") or []]
        active = bool(value.get("active", True))
    elif isinstance(value, list | tuple):
        aliases = [_clean_word(x) for x in value]
    elif isinstance(value, str):
        aliases = [_clean_word(value)] if value.strip() else []
    aliases = [a for a in aliases if a and a.lower() != name.lower()]
    return LexiconEntity(kind, name, tuple(dict.fromkeys([name, *aliases])), source=source, active=active)


def _load_json_entities(path: Path) -> list[LexiconEntity]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    entities: list[LexiconEntity] = []
    if isinstance(data, dict):
        for section, kind in _KIND_SECTIONS.items():
            raw_section = data.get(section)
            if isinstance(raw_section, dict):
                for canonical, value in raw_section.items():
                    entity = _entity_from_value(kind, canonical, value, str(path))
                    if entity is not None:
                        entities.append(entity)
            elif isinstance(raw_section, list):
                for item in raw_section:
                    if isinstance(item, dict):
                        canonical = item.get("canonical") or item.get("name")
                        entity = _entity_from_value(kind, str(canonical or ""), item, str(path))
                        if entity is not None:
                            entities.append(entity)
    return entities


def _merge_entities(entities: list[LexiconEntity]) -> tuple[LexiconEntity, ...]:
    by_key: dict[tuple[EntityKind, str], dict[str, Any]] = {}
    for entity in entities:
        if not entity.active:
            continue
        key = (entity.kind, entity.canonical.lower())
        slot = by_key.setdefault(key, {"kind": entity.kind, "canonical": entity.canonical, "aliases": [], "source": entity.source})
        slot["aliases"].extend(entity.aliases)
    out: list[LexiconEntity] = []
    for item in by_key.values():
        aliases = tuple(dict.fromkeys(_clean_word(x) for x in item["aliases"] if _clean_word(x)))
        out.append(LexiconEntity(item["kind"], item["canonical"], aliases, source=item["source"]))
    return tuple(out)


def load_entities() -> tuple[LexiconEntity, ...]:
    entities = list(_BUILTIN_ENTITIES)
    for path in _lexicon_paths():
        entities.extend(_load_json_entities(path))
    return _merge_entities(entities)


def teach_alias(kind: EntityKind, canonical: str, alias: str, *, source: str = "user") -> Path:
    """Persist a user-taught alias without overwriting synced lexicon data."""
    path = _user_aliases_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        data = {}
    section_name = {"app": "apps", "contact": "contacts", "project": "projects"}[kind]
    section = data.setdefault(section_name, {})
    current = section.setdefault(canonical, {"aliases": [], "source": source, "active": True})
    aliases = current.setdefault("aliases", [])
    clean_alias = _clean_word(alias)
    if clean_alias and clean_alias not in aliases:
        aliases.append(clean_alias)
    current["updated_at"] = int(time.time())
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path



def _user_aliases_path() -> Path:
    return _repo_root() / "data" / "voice" / "user_aliases.json"


def list_user_aliases() -> dict[str, Any]:
    path = _user_aliases_path()
    if not path.exists():
        return {"apps": {}, "contacts": {}, "projects": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"apps": {}, "contacts": {}, "projects": {}}
    return data if isinstance(data, dict) else {"apps": {}, "contacts": {}, "projects": {}}


def _write_user_aliases(data: dict[str, Any]) -> Path:
    path = _user_aliases_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def deactivate_alias(kind: EntityKind, canonical: str, alias: str) -> Path:
    data = list_user_aliases()
    section_name = {"app": "apps", "contact": "contacts", "project": "projects"}[kind]
    section = data.setdefault(section_name, {})
    current = section.setdefault(canonical, {"aliases": [], "source": "user", "active": True})
    aliases = [str(a) for a in current.get("aliases") or []]
    clean_alias = _clean_word(alias)
    current["aliases"] = [a for a in aliases if a != clean_alias]
    current["updated_at"] = int(time.time())
    return _write_user_aliases(data)


def bulk_import_aliases(items: list[dict[str, Any]], *, source: str = "bulk_import") -> Path:
    for item in items:
        kind = str(item.get("kind") or "").strip()
        canonical = _clean_word(item.get("canonical"))
        aliases = item.get("aliases") or []
        if kind not in {"app", "contact", "project"} or not canonical or not isinstance(aliases, list):
            continue
        for alias in aliases:
            teach_alias(kind, canonical, str(alias), source=source)  # type: ignore[arg-type]
    return _user_aliases_path()

def _clean_transcript(text: str) -> str:
    cleaned = str(text or "").strip()
    cleaned = _EMOJI_TAIL_RE.sub("", cleaned).strip()
    cleaned = _NOISE_LEADING_RE.sub("", cleaned).strip()
    cleaned = re.sub(r"([,.;!?\uff0c\u3002\uff01\uff1f\uff1b])\1+", r"\1", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _message_body_start(text: str) -> int:
    matches = list(_MESSAGE_START_RE.finditer(text))
    return matches[-1].end() if matches else len(text)


def _alias_pattern(alias: str) -> str:
    pieces = [re.escape(p) for p in alias.split()]
    if len(pieces) > 1:
        return r"\s*".join(pieces)
    if re.fullmatch(r"[A-Za-z]+", alias):
        return r"\s*".join(re.escape(ch) for ch in alias)
    return re.escape(alias)


def _entity_alias_regex(entity: LexiconEntity) -> re.Pattern[str]:
    alternatives = sorted((_alias_pattern(a) for a in entity.aliases), key=len, reverse=True)
    return re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(alternatives) + r")(?![A-Za-z0-9])", re.I)


def _entities_for(kind: EntityKind) -> tuple[LexiconEntity, ...]:
    return tuple(e for e in load_entities() if e.kind == kind)


def _context_windows(text: str, kind: EntityKind, protected_start: int) -> list[tuple[int, int]]:
    regexes = (_APP_CONTEXT_RE,) if kind == "app" else ((_CONTACT_CONTEXT_RE,) if kind == "contact" else (_PROJECT_CONTEXT_RE,))
    windows: list[tuple[int, int]] = []
    for regex in regexes:
        for match in regex.finditer(text):
            if match.start(1) >= protected_start:
                continue
            start = match.start(1)
            end = min(match.end(1), protected_start)
            cut = _SPLIT_CUT_RE.search(text[start:end])
            if cut:
                end = start + cut.start()
            if start < end:
                windows.append((start, end))
    return windows


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "").lower()


def _best_fuzzy_entity(token: str, kind: EntityKind) -> tuple[LexiconEntity | None, float, str]:
    compact = _norm(token)
    if len(compact) < 3:
        return None, 0.0, "too_short"
    best: tuple[LexiconEntity | None, float, str] = (None, 0.0, "")
    for entity in _entities_for(kind):
        for alias in entity.aliases:
            score = SequenceMatcher(None, compact, _norm(alias)).ratio()
            if score > best[1]:
                best = (entity, score, alias)
    threshold = 0.86 if re.fullmatch(r"[a-z0-9_.-]+", compact) else 0.90
    if best[0] is not None and best[1] >= threshold:
        return best
    return None, best[1], best[2]


def _replace_in_windows(text: str, *, kind: EntityKind, protected_start: int) -> tuple[str, list[EntityCorrection], list[SuspectToken]]:
    windows = _context_windows(text, kind, protected_start)
    replacements: list[tuple[int, int, str, EntityCorrection]] = []
    suspects: list[SuspectToken] = []
    for start, end in windows:
        window = text[start:end]
        direct_hit = False
        for entity in _entities_for(kind):
            regex = _entity_alias_regex(entity)
            for match in regex.finditer(window):
                original = match.group(0).strip()
                if not original:
                    continue
                direct_hit = True
                if original.lower() == entity.canonical.lower():
                    continue
                abs_start = start + match.start()
                abs_end = start + match.end()
                replacements.append((abs_start, abs_end, entity.canonical, EntityCorrection(kind, original, entity.canonical, abs_start, abs_end, f"{kind}_slot_alias", 0.92)))
        if not direct_hit:
            token = window.strip()
            entity, score, alias = _best_fuzzy_entity(token, kind)
            if entity is not None:
                replacements.append((start, end, entity.canonical, EntityCorrection(kind, token, entity.canonical, start, end, f"{kind}_slot_fuzzy:{alias}", round(float(score), 3))))
            elif token:
                candidates = [e.canonical for e in sorted(_entities_for(kind), key=lambda e: SequenceMatcher(None, _norm(token), _norm(e.canonical)).ratio(), reverse=True)[:3]]
                if candidates:
                    suspects.append(SuspectToken(token=token, kind=kind, candidates=candidates, reason="slot_unresolved", confidence=round(float(score), 3), start=start, end=end))

    if not replacements:
        return text, [], suspects
    replacements.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    selected: list[tuple[int, int, str, EntityCorrection]] = []
    last_end = -1
    for item in replacements:
        if item[0] < last_end:
            continue
        selected.append(item)
        last_end = item[1]
    out: list[str] = []
    cursor = 0
    corrections: list[EntityCorrection] = []
    for start, end, canonical, correction in selected:
        out.append(text[cursor:start])
        out.append(canonical)
        cursor = end
        corrections.append(correction)
    out.append(text[cursor:])
    return "".join(out), corrections, suspects


def correct_voice_entities(text: str) -> VoiceCorrectionResult:
    raw = str(text or "")
    cleaned = _clean_transcript(raw)
    corrected = cleaned
    corrections: list[EntityCorrection] = []
    suspect_tokens: list[SuspectToken] = []
    for kind in ("app", "contact", "project"):
        protected_start = _message_body_start(corrected)
        corrected, new_corrections, suspects = _replace_in_windows(corrected, kind=kind, protected_start=protected_start)
        corrections.extend(new_corrections)
        suspect_tokens.extend(suspects)
    result = VoiceCorrectionResult(raw_text=raw, cleaned_text=cleaned, corrected_text=corrected, corrections=corrections, suspect_tokens=suspect_tokens)
    if suspect_tokens:
        try:
            from l3_node.voice_llm_correction import run_bounded_llm_correction

            result = run_bounded_llm_correction(result)
        except Exception:
            pass
    return result


def export_hotwords() -> dict[str, int]:
    """Return a compact hotword view for STT integrations."""
    weights: dict[str, int] = {}
    for entity in load_entities():
        weight = 20 if entity.kind in {"app", "contact"} else 15
        weights[entity.canonical] = max(weights.get(entity.canonical, 0), weight)
        for alias in entity.aliases:
            if alias and alias != entity.canonical:
                weights[alias] = max(weights.get(alias, 0), max(5, weight - 10))
    return weights


def correction_payload(result: VoiceCorrectionResult) -> dict[str, Any]:
    return {
        "raw_text": result.raw_text,
        "cleaned_text": result.cleaned_text,
        "corrected_text": result.corrected_text,
        "corrections": [asdict(c) for c in result.corrections],
        "suspect_tokens": [asdict(s) for s in result.suspect_tokens],
    }
