#!/usr/bin/env python3
"""Jachin voice-module STT tester.

By default this script calls the same production voice module that Jachin uses:
the configured `voice_server` STT backend. That means it tests the active
production path, including DashScope Fun-ASR native hotwords when
`JACHIN_STT_BACKEND=cloud`, or Sherpa-ONNX hotwords when local STT is selected.

The old standalone Sherpa experiment runner is still available with
`--legacy-standalone`. It can test:

- Paraformer baseline transcription
- Zipformer Transducer transcription
- Zipformer hotwords A/B via from_transducer(hotwords_file=...)

Default model directories:
  D:\project\model\sherpa-onnx-paraformer-zh-2024-03-09
  D:\project\model\sherpa-onnx-zipformer-zh-en-2023-11-22

Examples:
  python scripts/test_sherpa_paraformer_stt.py --model-kind zipformer --file data/eval_wav/t1_clean/foo.wav
  python scripts/test_sherpa_paraformer_stt.py --model-kind zipformer --record 5 --save-wav data/eval_wav/t1_clean/manual.wav
  python scripts/test_sherpa_paraformer_stt.py --legacy-standalone --model-kind zipformer --file data/eval_wav/t1_clean/foo.wav --ab-hotwords
  python scripts/test_sherpa_paraformer_stt.py --list-devices
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from math import gcd
from pathlib import Path
from typing import Any

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL_ROOT = Path(os.getenv("JACHIN_SHERPA_MODEL_ROOT", r"D:\project\model"))
PARAFORMER_REPO_ID = "csukuangfj/sherpa-onnx-paraformer-zh-2024-03-09"
ZIPFORMER_REPO_ID = "csukuangfj/sherpa-onnx-zipformer-zh-en-2023-11-22"
DEFAULT_HOTWORDS = ROOT / "data" / "voice" / "sherpa_hotwords.txt"
DEFAULT_LEXICON = ROOT / "data" / "voice" / "domain_lexicon.json"
DEFAULT_USER_ALIASES = ROOT / "data" / "voice" / "user_aliases.json"
SAMPLE_RATE = 16000


@dataclass
class SherpaRun:
    label: str
    text: str
    latency_ms: int
    audio_sec: float
    rtf: float
    hotwords_count: int
    corrected_text: str = ""
    correction_confidence: float = 0.0
    correction_requires_confirmation: bool = False
    correction_reason: str = ""
    correction_candidates: list[dict[str, Any]] = field(default_factory=list)
    understanding: dict[str, Any] = field(default_factory=dict)
    error: str = ""


@dataclass
class SherpaReport:
    model_kind: str
    model_dir: str
    model_files: dict[str, str]
    provider: str
    num_threads: int
    source: str
    audio_sample_rate: int
    audio_sec: float
    hotwords_file: str
    hotwords_score: float
    lexicon_file: str
    correction_enabled: bool
    runs: list[SherpaRun] = field(default_factory=list)


@dataclass
class LexiconEntry:
    kind: str
    canonical: str
    aliases: list[str]


@dataclass
class CorrectionCandidate:
    kind: str
    canonical: str
    surface: str
    score: float
    reason: str
    matched_text: str = ""


@dataclass
class EntityCandidate:
    kind: str
    canonical: str
    surface: str
    matched_text: str
    span: list[int]
    score: float
    evidence: list[str] = field(default_factory=list)


@dataclass
class TaskCandidate:
    intent: str
    slots: dict[str, str]
    corrected_text: str
    score: float
    needs_confirmation: bool
    reasons: list[str] = field(default_factory=list)


@dataclass
class LlmRerankConfig:
    enabled: bool = False
    provider: str = "ollama"
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5:0.5b"
    timeout_sec: float = 8.0


def default_repo_id(model_kind: str) -> str:
    return ZIPFORMER_REPO_ID if model_kind == "zipformer" else PARAFORMER_REPO_ID


def default_model_dir(model_kind: str) -> Path:
    return DEFAULT_MODEL_ROOT / default_repo_id(model_kind).split("/")[-1]


def _require_module(name: str, install_hint: str) -> Any:
    try:
        return __import__(name)
    except ImportError as exc:
        raise SystemExit(f"Missing dependency {name}. Install with: {install_hint}") from exc


def _apply_proxy_env(proxy: str) -> None:
    if not proxy:
        return
    if "://" not in proxy:
        proxy = "http://" + proxy
    os.environ["HTTP_PROXY"] = proxy
    os.environ["HTTPS_PROXY"] = proxy
    os.environ["ALL_PROXY"] = proxy
    print(f"[proxy] {proxy}")


def download_model(repo_id: str, model_dir: Path, *, hf_endpoint: str = "", proxy: str = "", force_download: bool = False) -> Path:
    _apply_proxy_env(proxy)
    _require_module("huggingface_hub", "python -m pip install huggingface_hub")
    from huggingface_hub import snapshot_download

    if hf_endpoint:
        os.environ["HF_ENDPOINT"] = hf_endpoint.rstrip("/")
        print(f"[hf-endpoint] {os.environ['HF_ENDPOINT']}")
    model_dir.parent.mkdir(parents=True, exist_ok=True)
    print(f"[download] repo={repo_id}")
    print(f"[download] target={model_dir}")
    path = snapshot_download(repo_id=repo_id, local_dir=str(model_dir), resume_download=True, force_download=force_download)
    print(f"[download] done: {path}")
    return Path(path)


def _find_first(paths: list[Path]) -> Path | None:
    for path in paths:
        if path.is_file():
            return path
    return None


def find_paraformer_files(model_dir: Path) -> dict[str, Path]:
    if not model_dir.exists():
        raise FileNotFoundError(f"Model directory not found: {model_dir}")
    tokens = _find_first([model_dir / "tokens.txt", *sorted(model_dir.rglob("tokens.txt"))])
    model = _find_first([
        model_dir / "model.int8.onnx",
        model_dir / "model.onnx",
        model_dir / "model_quant.onnx",
        *sorted(model_dir.rglob("model.int8.onnx")),
        *sorted(model_dir.rglob("model.onnx")),
        *sorted(model_dir.rglob("*.onnx")),
    ])
    if tokens is None:
        raise FileNotFoundError(f"tokens.txt not found under: {model_dir}")
    if model is None:
        raise FileNotFoundError(f"Paraformer ONNX model not found under: {model_dir}")
    return {"model": model, "tokens": tokens}


def _pick_zipformer_component(model_dir: Path, name: str) -> Path | None:
    patterns = [
        f"{name}*.int8.onnx",
        f"{name}*.onnx",
        f"*{name}*.int8.onnx",
        f"*{name}*.onnx",
    ]
    for pattern in patterns:
        matches = sorted(model_dir.rglob(pattern))
        if matches:
            return matches[0]
    return None


def find_zipformer_files(model_dir: Path) -> dict[str, Path]:
    if not model_dir.exists():
        raise FileNotFoundError(f"Model directory not found: {model_dir}")
    tokens = _find_first([model_dir / "tokens.txt", *sorted(model_dir.rglob("tokens.txt"))])
    encoder = _pick_zipformer_component(model_dir, "encoder")
    decoder = _pick_zipformer_component(model_dir, "decoder")
    joiner = _pick_zipformer_component(model_dir, "joiner")
    missing = [name for name, value in {"tokens": tokens, "encoder": encoder, "decoder": decoder, "joiner": joiner}.items() if value is None]
    if missing:
        raise FileNotFoundError(f"Missing Zipformer files under {model_dir}: {', '.join(missing)}")
    out = {"encoder": encoder, "decoder": decoder, "joiner": joiner, "tokens": tokens}  # type: ignore[dict-item]
    bpe_vocab = _find_first([model_dir / "bpe.vocab", *sorted(model_dir.rglob("bpe.vocab"))])
    if bpe_vocab is not None:
        out["bpe_vocab"] = bpe_vocab
    return out


def ensure_default_hotwords(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    words = [
        "LARK :8.0",
        "Lark :8.0",
        "飞书 :6.0",
        "VIVIAN :8.0",
        "Vivian :8.0",
        "VIVI :5.0",
        "vivi :5.0",
        "薇薇安 :8.0",
        "微微安 :8.0",
        "JACHIN :5.0",
        "Jachin :5.0",
        "FEISHU :5.0",
        "Feishu :5.0",
        "VS CODE :4.0",
        "VS Code :4.0",
        "CHROME :4.0",
        "Chrome :4.0",
        "CODEX :4.0",
        "Codex :4.0",
    ]
    if path.exists():
        existing = [line.strip() for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines()]
        if all("?" not in line for line in existing if line):
            return
    path.write_text("\n".join(words) + "\n", encoding="utf-8")
    print(f"[hotwords] wrote default hotwords: {path}")


def read_hotwords(path: Path | None) -> tuple[str, int]:
    if path is None or not path.is_file():
        return "", 0
    words = [line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines()]
    words = [w for w in words if w and not w.startswith("#")]
    return "\n".join(words), len(words)


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_lexicon(lexicon_file: Path, user_aliases_file: Path | None = None) -> list[LexiconEntry]:
    data = _load_json(lexicon_file)
    user_aliases = _load_json(user_aliases_file) if user_aliases_file else {}
    entries: list[LexiconEntry] = []
    for kind in ("apps", "contacts", "projects"):
        for canonical, meta in (data.get(kind) or {}).items():
            if isinstance(meta, dict) and meta.get("active", True) is False:
                continue
            aliases = []
            if isinstance(meta, dict):
                aliases.extend(str(x) for x in meta.get("aliases", []) if str(x).strip())
            aliases.extend(str(x) for x in (user_aliases.get(kind) or {}).get(canonical, []) if str(x).strip())
            aliases.append(str(canonical))
            deduped = list(dict.fromkeys(alias.strip() for alias in aliases if alias.strip()))
            entries.append(LexiconEntry(kind=kind, canonical=str(canonical), aliases=deduped))
    return entries


def normalize_for_match(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "").lower())


def is_ascii_text(value: str) -> bool:
    return all(ord(ch) < 128 for ch in value)


def best_window_match(text: str, target: str) -> tuple[float, str]:
    text_norm = normalize_for_match(text)
    target_norm = normalize_for_match(target)
    if not text_norm or not target_norm:
        return 0.0, ""
    if target_norm in text_norm:
        return 1.0, target
    window_len = len(target_norm)
    best_score = 0.0
    best_text = ""
    for extra in range(0, 3):
        size = window_len + extra
        if size <= 0 or size > len(text_norm):
            continue
        for idx in range(0, len(text_norm) - size + 1):
            chunk = text_norm[idx : idx + size]
            score = SequenceMatcher(None, chunk, target_norm).ratio()
            if score > best_score:
                best_score = score
                best_text = chunk
    return best_score, best_text


INTENT_TRIGGER_ALIASES: dict[str, list[str]] = {
    "send_message": [
        "发消息",
        "发送消息",
        "发信息",
        "发送信息",
        "发条消息",
        "发一条消息",
        "消息内容",
        "sendmessage",
    ],
    "open_app": [
        "打开",
        "启动",
        "开启",
        "大开",
        "打给",
        "open",
    ],
    "find": [
        "找到",
        "找一下",
        "找",
        "查找",
        "搜索",
        "赵刀",
        "赵到",
        "照到",
        "造到",
        "早到",
        "招到",
        "遭到",
        "找刀",
        "find",
    ],
}

CANONICAL_INTENT_TRIGGER = {
    "send_message": "发消息",
    "open_app": "打开",
    "find": "找到",
}


def intent_trigger_pattern(intent: str) -> str:
    aliases = sorted(INTENT_TRIGGER_ALIASES.get(intent, []), key=len, reverse=True)
    return "|".join(re.escape(alias) for alias in aliases if alias)


def detect_intent(text: str) -> str:
    normalized = normalize_for_match(text)
    for intent in ("send_message", "open_app", "find"):
        if any(normalize_for_match(alias) in normalized for alias in INTENT_TRIGGER_ALIASES[intent]):
            return intent
    return "unknown"


def canonicalize_intent_trigger(text: str, intent: str) -> str:
    pattern = intent_trigger_pattern(intent)
    canonical = CANONICAL_INTENT_TRIGGER.get(intent, "")
    if not pattern or not canonical:
        return text
    return re.sub(pattern, canonical, text, count=1, flags=re.IGNORECASE)


def allowed_kinds_for_intent(intent: str) -> set[str]:
    if intent == "open_app":
        return {"apps"}
    if intent == "send_message":
        return {"apps", "contacts"}
    if intent == "find":
        return {"apps", "contacts", "projects"}
    return {"apps", "contacts", "projects"}


def suggest_surface(canonical: str, alias: str, matched_text: str) -> str:
    if normalize_for_match(alias) == normalize_for_match(canonical):
        return canonical
    if alias in {"飞书", "薇薇安", "微微安"}:
        return alias
    if is_ascii_text(alias) and is_ascii_text(canonical):
        return canonical
    if matched_text and is_ascii_text(matched_text):
        return canonical
    return alias


def lexicon_candidates(text: str, entries: list[LexiconEntry], intent: str) -> list[CorrectionCandidate]:
    allowed = allowed_kinds_for_intent(intent)
    text_norm = normalize_for_match(text)
    candidates: list[CorrectionCandidate] = []
    for entry in entries:
        if entry.kind not in allowed:
            continue
        best: CorrectionCandidate | None = None
        for alias in entry.aliases:
            alias_norm = normalize_for_match(alias)
            if not alias_norm:
                continue
            if alias_norm in text_norm:
                matched_text = alias
                score = 0.98 if normalize_for_match(entry.canonical) in text_norm else 0.92
                candidate = CorrectionCandidate(
                    kind=entry.kind,
                    canonical=entry.canonical,
                    surface=suggest_surface(entry.canonical, alias, matched_text),
                    score=score,
                    reason="alias_exact",
                    matched_text=matched_text,
                )
            else:
                fuzzy_score, matched_text = best_window_match(text, alias)
                threshold = 0.72 if is_ascii_text(alias) else 0.5
                if fuzzy_score < threshold:
                    continue
                candidate = CorrectionCandidate(
                    kind=entry.kind,
                    canonical=entry.canonical,
                    surface=suggest_surface(entry.canonical, alias, matched_text),
                    score=min(0.86, 0.45 + fuzzy_score * 0.45),
                    reason="alias_fuzzy",
                    matched_text=matched_text,
                )
            if best is None or candidate.score > best.score:
                best = candidate
        if best:
            candidates.append(best)

    if intent in {"find", "send_message"}:
        contacts = [entry for entry in entries if entry.kind == "contacts"]
        has_contact = any(candidate.kind == "contacts" for candidate in candidates)
        has_other_find_candidate = intent == "find" and any(candidate.kind != "contacts" for candidate in candidates)
        if len(contacts) == 1 and not has_contact and not has_other_find_candidate:
            candidates.append(
                CorrectionCandidate(
                    kind="contacts",
                    canonical=contacts[0].canonical,
                    surface=contacts[0].canonical,
                    score=0.62,
                    reason="context_single_contact",
                    matched_text="",
                )
            )
    return sorted(candidates, key=lambda item: item.score, reverse=True)


def replace_ascii_case_insensitive(text: str, needle: str, replacement: str) -> str:
    if not needle:
        return text
    return re.sub(re.escape(needle), replacement, text, flags=re.IGNORECASE)


def replace_best_cjk_window(text: str, target: str, replacement: str) -> str:
    if not target or not text:
        return text
    best_score = 0.0
    best_span: tuple[int, int] | None = None
    window_len = len(target)
    for extra in range(0, 3):
        size = window_len + extra
        if size <= 0 or size > len(text):
            continue
        for idx in range(0, len(text) - size + 1):
            chunk = text[idx : idx + size]
            if re.search(r"[A-Za-z0-9]", chunk):
                continue
            score = SequenceMatcher(None, chunk, target).ratio()
            if score > best_score:
                best_score = score
                best_span = (idx, idx + size)
    if best_span and best_score >= 0.5:
        start, end = best_span
        return text[:start] + replacement + text[end:]
    return text


def replace_context_slot(text: str, candidate: CorrectionCandidate, intent: str) -> str:
    if candidate.kind == "apps":
        if intent == "open_app":
            trigger = intent_trigger_pattern("open_app")
            pattern = rf"({trigger})([^，。,. ]+)$"
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return text[: match.start(1)] + CANONICAL_INTENT_TRIGGER["open_app"] + candidate.surface
        if intent == "find":
            trigger = intent_trigger_pattern("find")
            pattern = rf"({trigger})([^，。,. ]+)$"
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return text[: match.start(1)] + CANONICAL_INTENT_TRIGGER["find"] + candidate.surface
    if candidate.kind == "contacts":
        if intent == "send_message":
            pattern = r"(给|的)([^，。,. ]+?)(发消息|发送消息|消息内容)"
            match = re.search(pattern, text)
            if match:
                return text[: match.start(2)] + candidate.surface + text[match.end(2) :]
        if intent == "find":
            trigger = intent_trigger_pattern("find")
            pattern = rf"({trigger})([^，。,. ]+)$"
            match = re.search(pattern, text)
            if match:
                return text[: match.start(1)] + CANONICAL_INTENT_TRIGGER["find"] + candidate.surface
    return text


def apply_candidate(text: str, candidate: CorrectionCandidate, intent: str) -> str:
    if candidate.reason == "alias_fuzzy":
        context_corrected = replace_context_slot(text, candidate, intent)
        if context_corrected != text:
            return context_corrected

    corrected = text
    for token in (candidate.matched_text, candidate.canonical):
        if not token:
            continue
        if is_ascii_text(token):
            corrected = replace_ascii_case_insensitive(corrected, token, candidate.surface)
        elif token in corrected:
            corrected = corrected.replace(token, candidate.surface)
        else:
            corrected = replace_best_cjk_window(corrected, token, candidate.surface)
    if corrected == text and candidate.reason == "context_single_contact":
        corrected = replace_context_slot(text, candidate, intent)
    return corrected


def maybe_pinyin(value: str) -> str:
    try:
        from pypinyin import lazy_pinyin

        return "".join(lazy_pinyin(str(value or ""), errors="ignore")).lower()
    except Exception:
        return ""


def phonetic_fold(value: str) -> str:
    folded = normalize_for_match(value)
    folded = folded.replace("v", "w")
    folded = folded.replace("ph", "f")
    folded = folded.replace("ck", "k")
    return folded


def similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def extract_candidate_spans(text: str) -> list[tuple[int, int, str]]:
    spans: dict[tuple[int, int], str] = {}
    if text:
        spans[(0, len(text))] = text

    for match in re.finditer(r"[A-Za-z][A-Za-z0-9 _-]{1,}", text):
        spans[(match.start(), match.end())] = match.group(0)

    for match in re.finditer(r"[\u4e00-\u9fff]{2,}", text):
        segment = match.group(0)
        base = match.start()
        max_window = min(6, len(segment))
        for size in range(2, max_window + 1):
            for offset in range(0, len(segment) - size + 1):
                start = base + offset
                end = start + size
                spans[(start, end)] = text[start:end]

    return [(start, end, value) for (start, end), value in sorted(spans.items(), key=lambda item: (item[0][0], item[0][1]))]


def entity_surface(entry: LexiconEntry, alias: str, matched_text: str) -> str:
    return suggest_surface(entry.canonical, alias, matched_text)


def score_entity_span(span_text: str, alias: str) -> tuple[float, list[str]]:
    span_norm = normalize_for_match(span_text)
    alias_norm = normalize_for_match(alias)
    evidence: list[str] = []
    if not span_norm or not alias_norm:
        return 0.0, evidence
    if span_norm == alias_norm:
        return 0.98, ["exact"]
    if span_norm in alias_norm or alias_norm in span_norm:
        return 0.92, ["substring"]

    char_score = similarity(span_norm, alias_norm)
    best_score = char_score
    if char_score >= 0.72:
        evidence.append("char_similarity")

    span_pinyin = maybe_pinyin(span_text)
    alias_pinyin = maybe_pinyin(alias) if not is_ascii_text(alias) else normalize_for_match(alias)
    if span_pinyin and alias_pinyin:
        pinyin_score = similarity(span_pinyin, alias_pinyin)
        if pinyin_score > best_score:
            best_score = pinyin_score
        if pinyin_score >= 0.62:
            evidence.append("pinyin_similarity")

    folded_span = phonetic_fold(span_pinyin or span_norm)
    folded_alias = phonetic_fold(alias_pinyin or alias_norm)
    folded_score = similarity(folded_span, folded_alias)
    if folded_score > best_score:
        best_score = folded_score
    if folded_score >= 0.64:
        evidence.append("phonetic_similarity")

    return best_score, evidence


def global_entity_scan(text: str, entries: list[LexiconEntry]) -> list[EntityCandidate]:
    spans = extract_candidate_spans(text)
    best_by_entity: dict[tuple[str, str], EntityCandidate] = {}
    for start, end, span_text in spans:
        for entry in entries:
            for alias in [entry.canonical, *entry.aliases]:
                score, evidence = score_entity_span(span_text, alias)
                if score < 0.62:
                    continue
                if entry.kind == "projects" and score < 0.72:
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
                )
                key = (entry.kind, entry.canonical)
                current = best_by_entity.get(key)
                prefer_candidate = (
                    current is None
                    or candidate.score > current.score
                    or (
                        candidate.score == current.score
                        and not is_ascii_text(candidate.surface)
                        and is_ascii_text(current.surface)
                    )
                )
                if prefer_candidate:
                    best_by_entity[key] = candidate

    candidates = sorted(best_by_entity.values(), key=lambda item: item.score, reverse=True)
    return candidates[:10]


def weak_action_score(text: str, aliases: list[str]) -> float:
    text_norm = normalize_for_match(text)
    best = 0.0
    for alias in aliases:
        alias_norm = normalize_for_match(alias)
        if not alias_norm:
            continue
        if alias_norm in text_norm:
            best = max(best, 1.0)
            continue
        score, _matched = best_window_match(text, alias)
        alias_pinyin = maybe_pinyin(alias)
        text_pinyin = maybe_pinyin(text)
        pinyin_score = 0.0
        if alias_pinyin and text_pinyin:
            pinyin_score, _ = best_window_match(text_pinyin, alias_pinyin)
        best = max(best, score, pinyin_score)
    return round(best, 3)


def exact_action_score(text: str, aliases: list[str]) -> float:
    text_norm = normalize_for_match(text)
    if not text_norm:
        return 0.0
    for alias in aliases:
        alias_norm = normalize_for_match(alias)
        if alias_norm and alias_norm in text_norm:
            return 1.0
    return 0.0


def slot_key(kind: str) -> str:
    return {"apps": "app", "contacts": "contact", "projects": "project"}.get(kind, kind.rstrip("s"))


def replace_entity_spans(text: str, entities: list[EntityCandidate]) -> str:
    corrected = text
    for entity in sorted(entities, key=lambda item: item.span[0], reverse=True):
        start, end = entity.span
        if 0 <= start <= end <= len(corrected):
            corrected = corrected[:start] + entity.surface + corrected[end:]
    return corrected


def task_text(text: str, intent: str, entities: list[EntityCandidate], scores: dict[str, float]) -> str:
    if not entities:
        return text
    primary = entities[0]
    if intent == "open_app":
        return f"打开{primary.surface}"
    if intent == "find_app":
        return f"找到{primary.surface}"
    if intent == "find_contact":
        return f"找到{primary.surface}"
    if intent == "contact_interaction":
        return f"打开{primary.surface}会话"
    if intent == "send_message":
        return replace_entity_spans(text, entities)
    return replace_entity_spans(text, entities)


def make_task(
    text: str,
    intent: str,
    entities: list[EntityCandidate],
    score: float,
    reasons: list[str],
    action_scores: dict[str, float],
) -> TaskCandidate:
    slots = {slot_key(entity.kind): entity.canonical for entity in entities}
    fuzzy_entity = any("exact" not in entity.evidence and "substring" not in entity.evidence for entity in entities)
    needs_confirmation = score < 0.82 or intent in {"send_message", "contact_interaction"} or fuzzy_entity
    return TaskCandidate(
        intent=intent,
        slots=slots,
        corrected_text=task_text(text, intent, entities, action_scores),
        score=round(min(score, 0.99), 3),
        needs_confirmation=needs_confirmation,
        reasons=list(dict.fromkeys(reasons)),
    )


NON_TASK_EXACT_PHRASES = {
    "啊",
    "嗯",
    "哦",
    "好",
    "好的",
    "是的",
    "对",
    "没错",
    "谢谢",
    "不用了",
    "不要了",
    "不是这个",
}

NON_TASK_PATTERNS = [
    "你说的",
    "你说得",
    "都是对的",
    "挺好的",
    "我想一下",
    "刚才那个",
    "这个方案",
    "不是这个意思",
]


def is_strong_entity(entity: EntityCandidate) -> bool:
    return entity.score >= 0.86 or "exact" in entity.evidence or "substring" in entity.evidence


def is_weak_entity(entity: EntityCandidate) -> bool:
    return not is_strong_entity(entity)


def evidence_summary(entities: list[EntityCandidate], explicit_action_scores: dict[str, float]) -> dict[str, Any]:
    return {
        "has_explicit_action": any(score > 0 for score in explicit_action_scores.values()),
        "has_strong_entity": any(is_strong_entity(entity) for entity in entities),
        "has_weak_entity": any(is_weak_entity(entity) for entity in entities),
        "entity_count": len(entities),
        "top_entity_score": max((entity.score for entity in entities), default=0.0),
    }


def classify_utterance(text: str, entities: list[EntityCandidate], explicit_action_scores: dict[str, float]) -> dict[str, Any]:
    normalized = normalize_for_match(text)
    evidence = evidence_summary(entities, explicit_action_scores)
    reasons: list[str] = []

    if not normalized:
        return {"type": "non_task_audio", "task_likelihood": 0.0, "reasons": ["empty_text"], "evidence": evidence}

    if normalized in {normalize_for_match(item) for item in NON_TASK_EXACT_PHRASES}:
        return {
            "type": "non_task_audio" if len(normalized) <= 1 else "chat_or_statement",
            "task_likelihood": 0.02,
            "reasons": ["known_non_task_phrase"],
            "evidence": evidence,
        }

    if len(normalized) <= 1 and not evidence["has_strong_entity"]:
        return {"type": "non_task_audio", "task_likelihood": 0.02, "reasons": ["too_short_no_strong_entity"], "evidence": evidence}

    if any(pattern in normalized for pattern in (normalize_for_match(item) for item in NON_TASK_PATTERNS)):
        reasons.append("statement_like_text")

    if evidence["has_explicit_action"]:
        reasons.append("explicit_action")
    if evidence["has_strong_entity"]:
        reasons.append("strong_entity")
    if evidence["has_weak_entity"]:
        reasons.append("weak_entity")

    likelihood = 0.08
    if evidence["has_explicit_action"]:
        likelihood += 0.48
    if evidence["has_strong_entity"]:
        likelihood += 0.30
    elif evidence["has_weak_entity"]:
        likelihood += 0.08
    if len(normalized) <= 10 and evidence["has_strong_entity"]:
        likelihood += 0.10
    if "给" in text and ("发消息" in text or "内容是" in text):
        likelihood += 0.18
        reasons.append("send_message_structure")
    if reasons and "statement_like_text" in reasons:
        likelihood -= 0.45
    if evidence["has_weak_entity"] and not evidence["has_explicit_action"] and not evidence["has_strong_entity"]:
        likelihood -= 0.12
        reasons.append("weak_entity_only")

    likelihood = round(max(0.0, min(0.99, likelihood)), 3)
    utterance_type = "task_request" if likelihood >= 0.55 else "chat_or_statement"
    return {"type": utterance_type, "task_likelihood": likelihood, "reasons": list(dict.fromkeys(reasons)), "evidence": evidence}


def no_task_candidate(text: str, utterance: dict[str, Any]) -> TaskCandidate:
    likelihood = float(utterance.get("task_likelihood") or 0.0)
    score = round(max(0.01, min(0.99, 1.0 - likelihood)), 3)
    return TaskCandidate(
        intent="no_task",
        slots={},
        corrected_text=text,
        score=score,
        needs_confirmation=False,
        reasons=[str(item) for item in utterance.get("reasons", [])] or ["no_task_competition"],
    )


def generate_task_candidates(text: str, entities: list[EntityCandidate]) -> list[TaskCandidate]:
    normalized = normalize_for_match(text)
    is_short = len(normalized) <= 12
    explicit_action_scores = {
        "open": exact_action_score(text, INTENT_TRIGGER_ALIASES["open_app"] + ["进入", "切到", "切换到", "用一下"]),
        "find": exact_action_score(text, INTENT_TRIGGER_ALIASES["find"] + ["看下", "看一下", "查下", "联系", "呼叫"]),
        "send": exact_action_score(text, INTENT_TRIGGER_ALIASES["send_message"] + ["内容是", "告诉", "通知"]),
    }
    action_scores = {
        "open": weak_action_score(text, INTENT_TRIGGER_ALIASES["open_app"] + ["进入", "切到", "切换到", "用一下"]),
        "find": weak_action_score(text, INTENT_TRIGGER_ALIASES["find"] + ["看下", "看一下", "查下", "联系", "呼叫"]),
        "send": weak_action_score(text, INTENT_TRIGGER_ALIASES["send_message"] + ["内容是", "告诉", "通知"]),
    }
    by_kind: dict[str, list[EntityCandidate]] = {
        "apps": [item for item in entities if item.kind == "apps"],
        "contacts": [item for item in entities if item.kind == "contacts"],
        "projects": [item for item in entities if item.kind == "projects"],
    }
    send_context = bool(by_kind["contacts"]) and (action_scores["send"] >= 0.45 or "内容是" in text)
    utterance = classify_utterance(text, entities, explicit_action_scores)
    tasks: list[TaskCandidate] = []
    tasks.append(no_task_candidate(text, utterance))
    has_explicit_action = any(score > 0 for score in explicit_action_scores.values())
    top_entity_score = max((item.score for item in entities), default=0.0)
    short_high_conf_entity = len(normalized) <= 10 and top_entity_score >= 0.86
    weak_only = bool(entities) and all(is_weak_entity(entity) for entity in entities)
    if not has_explicit_action and not short_high_conf_entity:
        return sorted(tasks, key=lambda item: item.score, reverse=True)
    if weak_only and not has_explicit_action:
        return sorted(tasks, key=lambda item: item.score, reverse=True)

    if by_kind["apps"]:
        app = by_kind["apps"][0]
        open_score = 0.34 + app.score * 0.42 + action_scores["open"] * 0.18 + (0.06 if is_short else 0.0)
        if action_scores["find"] > action_scores["open"] + 0.1:
            open_score -= 0.08
        if send_context:
            open_score -= 0.15
        tasks.append(make_task(text, "open_app", [app], open_score, ["app_entity_anchor", "open_template"], action_scores))
        if action_scores["find"] >= 0.45 and not send_context:
            find_score = 0.32 + app.score * 0.42 + action_scores["find"] * 0.24
            tasks.append(make_task(text, "find_app", [app], find_score, ["app_entity_anchor", "find_like_action"], action_scores))

    if by_kind["contacts"]:
        contact = by_kind["contacts"][0]
        find_score = 0.32 + contact.score * 0.40 + action_scores["find"] * 0.18 + (0.08 if is_short else 0.0)
        tasks.append(make_task(text, "find_contact", [contact], find_score, ["contact_entity_anchor", "find_contact_template"], action_scores))

        interaction_score = 0.30 + contact.score * 0.36 + max(action_scores["open"], action_scores["find"]) * 0.24
        if "的" in text:
            interaction_score += 0.06
        tasks.append(
            make_task(
                text,
                "contact_interaction",
                [contact],
                interaction_score,
                ["contact_entity_anchor", "contact_interaction_template"],
                action_scores,
            )
        )

        if send_context:
            send_entities = ([by_kind["apps"][0]] if by_kind["apps"] else []) + [contact]
            send_score = 0.36 + contact.score * 0.34 + action_scores["send"] * 0.30
            if by_kind["apps"]:
                send_score += by_kind["apps"][0].score * 0.10
            tasks.append(make_task(text, "send_message", send_entities, send_score, ["contact_entity_anchor", "send_template"], action_scores))

    if by_kind["projects"]:
        project = by_kind["projects"][0]
        project_score = 0.30 + project.score * 0.40 + max(action_scores["open"], action_scores["find"]) * 0.18
        tasks.append(make_task(text, "open_project", [project], project_score, ["project_entity_anchor"], action_scores))

    return sorted(tasks, key=lambda item: item.score, reverse=True)[:8]


def select_understanding(text: str, entries: list[LexiconEntry]) -> dict[str, Any]:
    entities = global_entity_scan(text, entries)
    explicit_action_scores = {
        "open": exact_action_score(text, INTENT_TRIGGER_ALIASES["open_app"] + ["进入", "切到", "切换到", "用一下"]),
        "find": exact_action_score(text, INTENT_TRIGGER_ALIASES["find"] + ["看下", "看一下", "查下", "联系", "呼叫"]),
        "send": exact_action_score(text, INTENT_TRIGGER_ALIASES["send_message"] + ["内容是", "告诉", "通知"]),
    }
    utterance = classify_utterance(text, entities, explicit_action_scores)
    tasks = generate_task_candidates(text, entities)
    selected = tasks[0] if tasks else None
    return {
        "strategy": "global_entity_first",
        "asr_texts": [{"engine": "current", "text": text}],
        "utterance": utterance,
        "entity_candidates": [asdict(item) for item in entities],
        "task_candidates": [asdict(item) for item in tasks],
        "selected": asdict(selected) if selected else {},
    }


def llm_limited_rerank(understanding: dict[str, Any], config: LlmRerankConfig) -> dict[str, Any]:
    if not config.enabled or config.provider != "ollama":
        return understanding
    task_candidates = understanding.get("task_candidates") or []
    if len(task_candidates) <= 1:
        return understanding

    allowed_intents = [str(item.get("intent")) for item in task_candidates if item.get("intent")]
    prompt = {
        "instruction": (
            "You are a constrained reranker. Select exactly one intent from allowed_intents. "
            "Do not invent new intents, contacts, apps, slots, or message content. "
            "Prefer no_task when the utterance is chat, confirmation, rejection, noise, or weak evidence only. "
            "Return strict JSON: {\"selected_intent\": string, \"confidence\": number, \"needs_confirmation\": boolean, \"reason\": string}."
        ),
        "asr_texts": understanding.get("asr_texts", []),
        "utterance": understanding.get("utterance", {}),
        "entity_candidates": understanding.get("entity_candidates", [])[:5],
        "task_candidates": task_candidates[:6],
        "allowed_intents": allowed_intents,
    }
    body = {
        "model": config.ollama_model,
        "messages": [
            {"role": "system", "content": "Return only strict JSON."},
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
        "stream": False,
        "options": {"temperature": 0},
    }
    try:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            config.ollama_url.rstrip("/") + "/api/chat",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=config.timeout_sec) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        content = str((payload.get("message") or {}).get("content") or "").strip()
        decision = json.loads(content)
        selected_intent = str(decision.get("selected_intent") or "")
        if selected_intent not in allowed_intents:
            raise ValueError(f"LLM selected non-candidate intent: {selected_intent}")
        selected = next(item for item in task_candidates if item.get("intent") == selected_intent)
        selected = dict(selected)
        selected["score"] = round(float(decision.get("confidence") or selected.get("score") or 0.0), 3)
        selected["needs_confirmation"] = bool(decision.get("needs_confirmation", selected.get("needs_confirmation", True)))
        selected["reasons"] = list(dict.fromkeys([*(selected.get("reasons") or []), "llm_limited_rerank"]))
        out = dict(understanding)
        out["selected"] = selected
        out["llm_rerank"] = {
            "enabled": True,
            "provider": config.provider,
            "model": config.ollama_model,
            "selected_intent": selected_intent,
            "reason": str(decision.get("reason") or ""),
        }
        return out
    except Exception as exc:
        out = dict(understanding)
        out["llm_rerank"] = {
            "enabled": True,
            "provider": config.provider,
            "model": config.ollama_model,
            "error": str(exc),
            "fallback": "deterministic_ranker",
        }
        return out


def correct_with_lexicon(
    text: str,
    entries: list[LexiconEntry],
    llm_config: LlmRerankConfig | None = None,
) -> tuple[str, float, bool, str, list[dict[str, Any]], dict[str, Any]]:
    if not text.strip() or not entries:
        return text, 0.0, False, "disabled_or_empty", [], {}
    understanding = select_understanding(text, entries)
    if llm_config:
        understanding = llm_limited_rerank(understanding, llm_config)
    selected = understanding.get("selected") or {}
    entities = understanding.get("entity_candidates") or []
    if not selected:
        return text, 0.0, False, "no_task_candidate strategy=global_entity_first", [], understanding
    corrected = str(selected.get("corrected_text") or text)
    confidence = float(selected.get("score") or 0.0)
    requires_confirmation = bool(selected.get("needs_confirmation"))
    reason = "strategy=global_entity_first; selected=" + str(selected.get("intent") or "unknown")
    if selected.get("intent") == "no_task":
        return text, round(confidence, 3), False, reason, [], understanding
    return corrected, round(confidence, 3), requires_confirmation, reason, entities[:5], understanding


def load_audio(path: Path) -> tuple[Any, int]:
    np = _require_module("numpy", "python -m pip install numpy soundfile scipy")
    sf = _require_module("soundfile", "python -m pip install soundfile")
    data, sample_rate = sf.read(str(path), dtype="float32", always_2d=False)
    audio = np.asarray(data, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sample_rate != SAMPLE_RATE:
        try:
            from scipy.signal import resample_poly

            g = gcd(int(sample_rate), SAMPLE_RATE)
            audio = resample_poly(audio, SAMPLE_RATE // g, int(sample_rate) // g).astype(np.float32)
        except Exception:
            target_len = max(1, int(len(audio) * SAMPLE_RATE / sample_rate))
            x_old = np.linspace(0.0, 1.0, len(audio), dtype=np.float64)
            x_new = np.linspace(0.0, 1.0, target_len, dtype=np.float64)
            audio = np.interp(x_new, x_old, audio.astype(np.float64)).astype(np.float32)
        print(f"[audio] resampled {sample_rate} Hz -> {SAMPLE_RATE} Hz")
        sample_rate = SAMPLE_RATE
    return audio, int(sample_rate)


def record_wav_bytes(duration_sec: float, device: int | None) -> bytes:
    sd = _require_module("sounddevice", "python -m pip install sounddevice")
    sf = _require_module("soundfile", "python -m pip install soundfile")
    duration_sec = max(0.5, min(float(duration_sec), 120.0))
    frames = int(duration_sec * SAMPLE_RATE)
    print(f"[record] recording {duration_sec:.1f}s @ {SAMPLE_RATE} Hz")
    audio = sd.rec(frames, samplerate=SAMPLE_RATE, channels=1, dtype="float32", device=device)
    sd.wait()
    buf = io.BytesIO()
    sf.write(buf, audio, SAMPLE_RATE, subtype="PCM_16", format="WAV")
    return buf.getvalue()


def record_ptt_bytes(device: int | None) -> bytes:
    np = _require_module("numpy", "python -m pip install numpy")
    sd = _require_module("sounddevice", "python -m pip install sounddevice")
    sf = _require_module("soundfile", "python -m pip install soundfile")
    chunks: list[Any] = []

    def callback(indata, _frames, _time_info, status) -> None:
        if status:
            print(f"[record] {status}", file=sys.stderr)
        chunks.append(indata.copy())

    print("[ptt] Press Enter to start recording")
    input()
    print("[ptt] Recording. Press Enter to stop")
    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
        device=device,
        blocksize=1024,
        callback=callback,
    ):
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            pass
    if not chunks:
        return b""
    audio = np.concatenate(chunks, axis=0)
    buf = io.BytesIO()
    sf.write(buf, audio, SAMPLE_RATE, subtype="PCM_16", format="WAV")
    return buf.getvalue()


def wav_bytes_to_audio(wav_bytes: bytes) -> tuple[Any, int]:
    sf = _require_module("soundfile", "python -m pip install soundfile")
    import numpy as np

    data, sample_rate = sf.read(io.BytesIO(wav_bytes), dtype="float32", always_2d=False)
    audio = np.asarray(data, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    return audio, int(sample_rate)


def build_paraformer(model_dir: Path, *, num_threads: int, provider: str, debug: bool):
    sherpa_onnx = _require_module("sherpa_onnx", "python -m pip install sherpa-onnx")
    files = find_paraformer_files(model_dir)
    print(f"[model] paraformer={files['model']}")
    print(f"[model] tokens={files['tokens']}")
    t0 = time.perf_counter()
    recognizer = sherpa_onnx.OfflineRecognizer.from_paraformer(
        paraformer=str(files["model"]),
        tokens=str(files["tokens"]),
        num_threads=num_threads,
        sample_rate=SAMPLE_RATE,
        feature_dim=80,
        decoding_method="greedy_search",
        provider=provider,
        debug=debug,
    )
    print(f"[model] loaded in {(time.perf_counter() - t0) * 1000:.0f} ms")
    return recognizer, files


def build_zipformer(model_dir: Path, *, num_threads: int, provider: str, debug: bool, hotwords_file: Path | None, hotwords_score: float):
    sherpa_onnx = _require_module("sherpa_onnx", "python -m pip install sherpa-onnx")
    files = find_zipformer_files(model_dir)
    print(f"[model] encoder={files['encoder']}")
    print(f"[model] decoder={files['decoder']}")
    print(f"[model] joiner={files['joiner']}")
    print(f"[model] tokens={files['tokens']}")
    t0 = time.perf_counter()
    recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
        encoder=str(files["encoder"]),
        decoder=str(files["decoder"]),
        joiner=str(files["joiner"]),
        tokens=str(files["tokens"]),
        num_threads=num_threads,
        sample_rate=SAMPLE_RATE,
        feature_dim=80,
        decoding_method="modified_beam_search" if hotwords_file else "greedy_search",
        max_active_paths=4,
        hotwords_file=str(hotwords_file or ""),
        hotwords_score=hotwords_score,
        modeling_unit="bpe" if hotwords_file and files.get("bpe_vocab") else "cjkchar",
        bpe_vocab=str(files.get("bpe_vocab") or ""),
        provider=provider,
        debug=debug,
    )
    print(f"[model] loaded in {(time.perf_counter() - t0) * 1000:.0f} ms")
    return recognizer, files


def transcribe_once(recognizer: Any, audio: Any, sample_rate: int, *, label: str, hotwords_count: int) -> SherpaRun:
    audio_sec = len(audio) / max(sample_rate, 1)
    try:
        stream = recognizer.create_stream()
        stream.accept_waveform(sample_rate, audio)
        t0 = time.perf_counter()
        recognizer.decode_stream(stream)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        text = str(stream.result.text or "").strip()
        rtf = (latency_ms / 1000.0) / max(audio_sec, 0.001)
        return SherpaRun(
            label=label,
            text=text,
            latency_ms=latency_ms,
            audio_sec=round(audio_sec, 3),
            rtf=round(rtf, 3),
            hotwords_count=hotwords_count,
        )
    except Exception as exc:
        return SherpaRun(
            label=label,
            text="",
            latency_ms=0,
            audio_sec=round(audio_sec, 3),
            rtf=0.0,
            hotwords_count=hotwords_count,
            error=str(exc),
        )


def list_devices() -> int:
    sd = _require_module("sounddevice", "python -m pip install sounddevice")
    print(sd.query_devices())
    print(f"\nDefault input device: {sd.default.device[0]}")
    return 0


def _make_jachin_stt_service() -> tuple[Any, Any]:
    voice_server_dir = ROOT / "voice_server"
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    if str(voice_server_dir) not in sys.path:
        sys.path.insert(0, str(voice_server_dir))
    try:
        from config import load_config
    except Exception as exc:
        raise SystemExit(f"Failed to import Jachin voice modules from {voice_server_dir}: {exc}") from exc
    cfg = load_config()
    try:
        if cfg.stt_backend == "cloud":
            from services.cloud_stt_service import CloudSttService

            service = CloudSttService(
                api_key=cfg.dashscope_api_key,
                api_base=cfg.dashscope_api_base,
                ws_api_base=cfg.dashscope_ws_api_base,
                model=cfg.stt_model,
                realtime_model=cfg.stt_realtime_model,
                hotword_model=cfg.stt_hotword_model,
                file_model=cfg.stt_file_model,
                vocabulary_id=cfg.stt_vocabulary_id,
                vocabulary_prefix=cfg.stt_vocabulary_prefix,
                auto_sync_vocabulary=cfg.stt_auto_sync_vocabulary,
                workspace=cfg.dashscope_workspace_id,
                language=cfg.stt_language,
            )
        else:
            from services.stt_service import SttService

            service = SttService(cfg.stt_dir)
    except Exception as exc:
        raise SystemExit(f"Failed to create Jachin STT service: {exc}") from exc
    return cfg, service


def _read_input_wav_bytes(args: argparse.Namespace) -> tuple[bytes, str]:
    if args.file:
        return args.file.read_bytes(), str(args.file)
    if args.ptt or args.record is None:
        wav_bytes = record_ptt_bytes(args.device)
        source = "microphone-ptt"
    else:
        wav_bytes = record_wav_bytes(args.record, args.device)
        source = f"microphone-{args.record:.1f}s"
    if not wav_bytes:
        raise SystemExit("[error] no audio captured")
    if args.save_wav:
        args.save_wav.parent.mkdir(parents=True, exist_ok=True)
        args.save_wav.write_bytes(wav_bytes)
        print(f"[save] {args.save_wav}")
    return wav_bytes, source


def run_jachin_voice_module(args: argparse.Namespace) -> int:
    cfg, service = _make_jachin_stt_service()
    model_ref = getattr(service, "model_path", "") or getattr(service, "model_name", "unknown")
    wav_bytes, source = _read_input_wav_bytes(args)

    if args.debug:
        if args.model_kind != "zipformer":
            print(f"[mode] Jachin voice module ignores --model-kind={args.model_kind}; configured STT is {service.model_name}.")
        else:
            print(f"[mode] Jachin voice module: {service.model_name}")
        if args.ab_hotwords:
            print("[A/B] skipped: production Jachin STT always uses configured hotwords from SttHotwordProvider.")
        if args.no_hotwords:
            print("[hotwords] --no-hotwords ignored in Jachin voice-module mode. Use --legacy-standalone for raw experiments.")
        if args.llm_rerank:
            print("[llm-rerank] ignored in Jachin voice-module mode unless the production module enables it.")
        print(f"[source] {source}")
        print(f"[stt_backend] {cfg.stt_backend}")
        print(f"[model_ref] {model_ref}")
    if not service.ready:
        print(f"[error] Jachin STT model not ready: {service.model_path}", file=sys.stderr)
        return 2

    t0 = time.perf_counter()
    result = service.transcribe(wav_bytes)
    wall_ms = int((time.perf_counter() - t0) * 1000)
    selected = (result.understanding or {}).get("selected") or {}
    utterance = (result.understanding or {}).get("utterance") or {}
    entities = (result.understanding or {}).get("entity_candidates") or []
    tasks = (result.understanding or {}).get("task_candidates") or []
    audio_sec = round(result.duration_ms / 1000.0, 3)
    rtf = round((wall_ms / 1000.0) / max(audio_sec, 0.001), 3)
    selected_type = str(selected.get("type") or "")
    status_label = {
        "clarification_required": "需要追问",
        "task_requires_confirmation": "需要确认",
        "task_ready": "已理解",
        "no_task": "普通文本",
    }.get(selected_type, "已识别")
    user_message = str(result.user_message or selected.get("question") or "").strip()

    print()
    print("-- Jachin Voice Test --")
    print(f"识别文本: {result.raw_text or result.text or '(empty)'}")
    if user_message and user_message != result.text:
        print(f"系统回复: {user_message}")
    else:
        print(f"整理结果: {result.text or '(empty)'}")
    print(f"状态    : {status_label}")
    print(f"耗时    : {wall_ms} ms")
    if args.debug:
        print()
        print("-- Debug Details --")
        print(f"  text          : {result.text or '(empty)'}")
        print(f"  user_message  : {user_message or '(empty)'}")
        print(f"  confidence    : {result.confidence}")
        print(f"  duration_ms   : {result.duration_ms}")
        print(f"  rtf           : {rtf}")
        print(f"  backend       : {result.backend}")
        print(f"  hotword_count : {result.hotword_count}")
        print(f"  hotword_status: {result.hotword_status}")
        if result.hotword_sources:
            print(f"  hotword_sources: {', '.join(result.hotword_sources)}")
        if utterance:
            print(f"  utterance     : {utterance.get('type')} task_likelihood={utterance.get('task_likelihood')}")
        if selected:
            debug_type = selected.get("type") or "task_candidate"
            print(f"  selected_task : {debug_type}:{selected.get('intent')} {selected.get('slots')} @ {selected.get('score')}")
            print(f"  requires_confirmation: {selected.get('needs_confirmation')}")
            if selected.get("missing_slots"):
                print(f"  missing_slots : {selected.get('missing_slots')}")
            if selected.get("question"):
                print(f"  question      : {selected.get('question')}")
        if entities:
            compact_entities = [
                f"{item.get('kind')}:{item.get('canonical')}@{item.get('score')}[{'+'.join(item.get('evidence') or [])}]"
                for item in entities[:3]
            ]
            print(f"  entity_candidates: {', '.join(compact_entities)}")
        if tasks:
            compact_tasks = [f"{item.get('intent')}@{item.get('score')}" for item in tasks[:3]]
            print(f"  task_candidates: {', '.join(compact_tasks)}")

    if args.json_out:
        payload = {
            "mode": "jachin_voice_module",
            "stt_backend": cfg.stt_backend,
            "model_ref": str(model_ref),
            "source": source,
            "wall_latency_ms": wall_ms,
            "rtf": rtf,
            "result": {
                "text": result.text,
                "raw_text": result.raw_text,
                "user_message": result.user_message,
                "confidence": result.confidence,
                "duration_ms": result.duration_ms,
                "language": result.language,
                "backend": result.backend,
                "hotword_count": result.hotword_count,
                "hotword_status": result.hotword_status,
                "hotword_sources": list(result.hotword_sources),
                "understanding": result.understanding,
            },
        }
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[report] {args.json_out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Test the Jachin voice-module STT path")
    parser.add_argument("--legacy-standalone", action="store_true", help="Use the old standalone Sherpa experiment runner")
    parser.add_argument("--model-kind", choices=["paraformer", "zipformer"], default="zipformer")
    parser.add_argument("--model-dir", type=Path, default=None)
    parser.add_argument("--repo-id", default="")
    parser.add_argument("--download", action="store_true", help="Download model to --model-dir with huggingface_hub")
    parser.add_argument("--force-download", action="store_true", help="Force re-download model files")
    parser.add_argument("--proxy", default=os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY") or "", help="Proxy, e.g. http://127.0.0.1:8800")
    parser.add_argument("--hf-endpoint", default=os.getenv("HF_ENDPOINT", ""), help="Optional Hugging Face mirror endpoint")
    parser.add_argument("--file", type=Path, help="WAV file to transcribe")
    parser.add_argument("--record", type=float, help="Record N seconds from microphone")
    parser.add_argument("--ptt", action="store_true", help="Press Enter to start/stop recording")
    parser.add_argument("--device", type=int, default=None)
    parser.add_argument("--save-wav", type=Path)
    parser.add_argument("--hotwords-file", type=Path, default=DEFAULT_HOTWORDS)
    parser.add_argument("--hotwords-score", type=float, default=4.0)
    parser.add_argument("--no-hotwords", action="store_true")
    parser.add_argument("--ab-hotwords", action="store_true", help="Compatibility flag; real A/B only runs with --legacy-standalone")
    parser.add_argument("--lexicon-file", type=Path, default=DEFAULT_LEXICON)
    parser.add_argument("--user-aliases-file", type=Path, default=DEFAULT_USER_ALIASES)
    parser.add_argument("--no-correction", action="store_true", help="Disable experimental lexicon correction output")
    parser.add_argument("--llm-rerank", action="store_true", help="Use optional constrained LLM rerank over task candidates")
    parser.add_argument("--llm-provider", default="ollama", choices=["ollama"])
    parser.add_argument("--ollama-url", default=os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434"))
    parser.add_argument("--ollama-model", default=os.getenv("JACHIN_STT_RERANK_MODEL", "qwen2.5:0.5b"))
    parser.add_argument("--llm-timeout", type=float, default=8.0)
    parser.add_argument("--num-threads", type=int, default=max(1, min(4, os.cpu_count() or 1)))
    parser.add_argument("--provider", default="cpu", choices=["cpu", "cuda", "coreml"])
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--list-devices", action="store_true")
    args = parser.parse_args()

    if args.list_devices:
        return list_devices()

    if not args.legacy_standalone:
        return run_jachin_voice_module(args)

    model_dir = args.model_dir or default_model_dir(args.model_kind)
    repo_id = args.repo_id or default_repo_id(args.model_kind)

    if args.download:
        try:
            download_model(repo_id, model_dir, hf_endpoint=args.hf_endpoint, proxy=args.proxy, force_download=args.force_download)
        except Exception as exc:
            print(f"[download failed] {exc}", file=sys.stderr)
            print(f"Put the model files under: {model_dir}", file=sys.stderr)
            return 2

    hotwords_text = ""
    hotwords_count = 0
    hotwords_file: Path | None = None
    if not args.no_hotwords:
        ensure_default_hotwords(args.hotwords_file)
        hotwords_text, hotwords_count = read_hotwords(args.hotwords_file)
        hotwords_file = args.hotwords_file if hotwords_text else None

    lexicon_entries: list[LexiconEntry] = []
    if not args.no_correction:
        lexicon_entries = load_lexicon(args.lexicon_file, args.user_aliases_file)
        print(f"[lexicon] entries={len(lexicon_entries)} file={args.lexicon_file}")
    llm_config = LlmRerankConfig(
        enabled=bool(args.llm_rerank),
        provider=args.llm_provider,
        ollama_url=args.ollama_url,
        ollama_model=args.ollama_model,
        timeout_sec=args.llm_timeout,
    )
    if args.llm_rerank:
        print(f"[llm-rerank] provider={llm_config.provider} model={llm_config.ollama_model}")

    source = ""
    if args.file:
        source = str(args.file)
        audio, sample_rate = load_audio(args.file)
    else:
        if args.ptt or args.record is None:
            wav_bytes = record_ptt_bytes(args.device)
            source = "microphone-ptt"
        else:
            wav_bytes = record_wav_bytes(args.record, args.device)
            source = f"microphone-{args.record:.1f}s"
        if not wav_bytes:
            print("[error] no audio captured", file=sys.stderr)
            return 1
        if args.save_wav:
            args.save_wav.parent.mkdir(parents=True, exist_ok=True)
            args.save_wav.write_bytes(wav_bytes)
            print(f"[save] {args.save_wav}")
        audio, sample_rate = wav_bytes_to_audio(wav_bytes)

    runs: list[SherpaRun] = []
    files: dict[str, Path]
    if args.model_kind == "paraformer":
        recognizer, files = build_paraformer(model_dir, num_threads=args.num_threads, provider=args.provider, debug=args.debug)
        if hotwords_file and args.ab_hotwords:
            print("[hotwords] paraformer note: Sherpa-ONNX Paraformer does not support contextual biasing; decoding baseline only.")
        runs.append(transcribe_once(recognizer, audio, sample_rate, label="paraformer_without_hotwords", hotwords_count=0))
    else:
        recognizer_base, files = build_zipformer(
            model_dir,
            num_threads=args.num_threads,
            provider=args.provider,
            debug=args.debug,
            hotwords_file=None,
            hotwords_score=args.hotwords_score,
        )
        runs.append(transcribe_once(recognizer_base, audio, sample_rate, label="zipformer_without_hotwords", hotwords_count=0))
        if args.ab_hotwords and hotwords_file:
            recognizer_hot, _ = build_zipformer(
                model_dir,
                num_threads=args.num_threads,
                provider=args.provider,
                debug=args.debug,
                hotwords_file=hotwords_file,
                hotwords_score=args.hotwords_score,
            )
            runs.append(transcribe_once(recognizer_hot, audio, sample_rate, label="zipformer_with_hotwords", hotwords_count=hotwords_count))

    if lexicon_entries:
        for run in runs:
            if run.error:
                continue
            (
                run.corrected_text,
                run.correction_confidence,
                run.correction_requires_confirmation,
                run.correction_reason,
                run.correction_candidates,
                run.understanding,
            ) = correct_with_lexicon(run.text, lexicon_entries, llm_config)

    report = SherpaReport(
        model_kind=args.model_kind,
        model_dir=str(model_dir),
        model_files={k: str(v) for k, v in files.items()},
        provider=args.provider,
        num_threads=args.num_threads,
        source=source,
        audio_sample_rate=sample_rate,
        audio_sec=round(len(audio) / max(sample_rate, 1), 3),
        hotwords_file=str(hotwords_file or ""),
        hotwords_score=args.hotwords_score,
        lexicon_file=str(args.lexicon_file),
        correction_enabled=bool(lexicon_entries),
        runs=runs,
    )

    print()
    print(f"-- Sherpa-ONNX {args.model_kind} STT --")
    for run in runs:
        print(f"[{run.label}]")
        print(f"  text          : {run.text or '(empty)'}")
        print(f"  latency_ms    : {run.latency_ms}")
        print(f"  audio_sec     : {run.audio_sec}")
        print(f"  rtf           : {run.rtf}")
        print(f"  hotwords_count: {run.hotwords_count}")
        if lexicon_entries:
            print(f"  corrected_text: {run.corrected_text or '(empty)'}")
            print(f"  correction_confidence: {run.correction_confidence}")
            print(f"  requires_confirmation: {run.correction_requires_confirmation}")
            print(f"  correction_reason: {run.correction_reason}")
            utterance = run.understanding.get("utterance") if run.understanding else {}
            if utterance:
                print(f"  utterance     : {utterance.get('type')} task_likelihood={utterance.get('task_likelihood')}")
            selected = run.understanding.get("selected") if run.understanding else {}
            if selected:
                print(f"  selected_task : {selected.get('intent')} {selected.get('slots')} @ {selected.get('score')}")
            llm_rerank = run.understanding.get("llm_rerank") if run.understanding else {}
            if llm_rerank:
                status = llm_rerank.get("selected_intent") or llm_rerank.get("error") or "ok"
                print(f"  llm_rerank    : {status}")
            if run.correction_candidates:
                compact = [
                    f"{item.get('kind')}:{item.get('canonical')}@{item.get('score')}[{'+'.join(item.get('evidence') or [])}]"
                    for item in run.correction_candidates[:3]
                ]
                print(f"  entity_candidates: {', '.join(compact)}")
            task_candidates = (run.understanding or {}).get("task_candidates") or []
            if task_candidates:
                compact_tasks = [
                    f"{item.get('intent')}@{item.get('score')}"
                    for item in task_candidates[:3]
                ]
                print(f"  task_candidates: {', '.join(compact_tasks)}")
        if run.error:
            print(f"  error         : {run.error}")
    if args.ab_hotwords and len(runs) == 2:
        changed = runs[0].text != runs[1].text
        print(f"[A/B] hotwords_changed_text: {changed}")
        print(f"[A/B] hotwords_supported: {args.model_kind == 'zipformer'}")

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[report] {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
