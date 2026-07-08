from __future__ import annotations

import json
import os
import re
import site
import sysconfig
from functools import lru_cache
from pathlib import Path
from typing import Any


MODEL_ID = "com.jachin.model.qwen2-5-0-5b-instruct-gguf-q4-k-m"
MODEL_FILE = "Qwen2.5-0.5B-Instruct-Q4_K_M.gguf"
TEMPLATE_ENGINE_ID = "local_scene_templates_v8"
FAST_TEMPLATE_ENGINE_ID = "local_fast_examples_v7"
MAX_HISTORY_ITEMS = 200
MIN_WORDS = 5
MAX_WORDS = 18
_WORD_RE = re.compile(r"[a-z]+(?:'[a-z]+)?")
QUALITY_PASS_SCORE = 0.9
QUALITY_REGEN_SCORE = 0.82
MAX_LLM_ROUNDS = 2
MODEL_FIRST_DEFAULT = "1"
_CUDA_DLL_DIRS_ADDED = False


def _home() -> Path:
    raw = os.environ.get("JACHIN_HOME")
    if raw:
        return Path(raw)
    return Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or ".") / ".jachin"


def _add_cuda_dll_dirs() -> list[str]:
    """Make pip-installed NVIDIA CUDA DLLs visible to llama-cpp on Windows."""
    global _CUDA_DLL_DIRS_ADDED
    added: list[str] = []
    if os.name != "nt":
        return added
    candidates: list[Path] = []
    for root in dict.fromkeys(
        [
            sysconfig.get_paths().get("purelib", ""),
            sysconfig.get_paths().get("platlib", ""),
            *site.getsitepackages(),
        ]
    ):
        if not root:
            continue
        base = Path(root) / "nvidia"
        candidates.extend(
            [
                base / "cuda_runtime" / "bin",
                base / "cublas" / "bin",
                base / "cuda_nvrtc" / "bin",
            ]
        )
    for path in candidates:
        if not path.is_dir():
            continue
        text = str(path)
        if text not in os.environ.get("PATH", ""):
            os.environ["PATH"] = text + os.pathsep + os.environ.get("PATH", "")
        if not _CUDA_DLL_DIRS_ADDED and hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(text)
            except Exception:
                pass
        added.append(text)
    _CUDA_DLL_DIRS_ADDED = True
    return added


def _model_path() -> Path:
    installed = _home() / "models" / MODEL_ID / "model" / MODEL_FILE
    if installed.is_file():
        return installed
    repo_root = Path(__file__).resolve().parents[3]
    dev_model = repo_root / "models_repo" / MODEL_ID / "model" / MODEL_FILE
    if dev_model.is_file():
        return dev_model
    return installed


def _cache_path() -> Path:
    return _home() / "data" / "english_vocab" / "example_generator_cache.json"


def _history_path() -> Path:
    return _home() / "data" / "english_vocab" / "example_generator_history.json"


def _cache_key(word: str, book_id: str | None) -> str:
    return f"{book_id or 'daily_life_ngsl'}:{word.strip().lower()}"


def _read_cache() -> dict[str, Any]:
    path = _cache_path()
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_cache(cache: dict[str, Any]) -> None:
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_history() -> dict[str, Any]:
    path = _history_path()
    if not path.is_file():
        return {"recent_examples": [], "recent_signatures": [], "recent_template_ids": []}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"recent_examples": [], "recent_signatures": [], "recent_template_ids": []}
    if not isinstance(raw, dict):
        return {"recent_examples": [], "recent_signatures": [], "recent_template_ids": []}
    examples = raw.get("recent_examples")
    signatures = raw.get("recent_signatures")
    template_ids = raw.get("recent_template_ids")
    return {
        "recent_examples": [str(x) for x in examples] if isinstance(examples, list) else [],
        "recent_signatures": [str(x) for x in signatures] if isinstance(signatures, list) else [],
        "recent_template_ids": [str(x) for x in template_ids] if isinstance(template_ids, list) else [],
    }


def _write_history(history: dict[str, Any]) -> None:
    path = _history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "recent_examples": list(dict.fromkeys(str(x) for x in history.get("recent_examples") or []))[-MAX_HISTORY_ITEMS:],
        "recent_signatures": list(
            dict.fromkeys(str(x) for x in history.get("recent_signatures") or [])
        )[-MAX_HISTORY_ITEMS:],
        "recent_template_ids": list(
            dict.fromkeys(str(x) for x in history.get("recent_template_ids") or [])
        )[-MAX_HISTORY_ITEMS:],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _scene(book_id: str | None) -> str:
    if book_id == "daily_life_ngsl":
        return "daily spoken life: meals, shopping, travel, home, weather, health"
    if book_id == "workplace":
        return "workplace communication: meetings, reports, feedback, deadlines, collaboration"
    if book_id == "computer_science":
        return "software engineering: coding, debugging, deployment, systems, data"
    if book_id == "ielts_academic":
        return "IELTS academic writing: society, education, environment, economy"
    if book_id == "toefl_academic":
        return "TOEFL campus and lecture scenarios: classes, research, labs, assignments"
    return "natural modern English"


def _extract_json(text: str) -> dict[str, Any]:
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    return json.loads(text)


def _clean_sentence(text: str, word: str) -> str:
    clean = re.sub(r"\s+", " ", (text or "").strip().strip('"'))
    if not clean:
        raise ValueError("model returned an empty example")
    clean = clean.replace(" ,", ",").replace(" .", ".")
    if clean[-1] not in ".!?":
        clean += "."
    if clean_word_token(word) not in re_words(clean):
        raise ValueError("model example does not contain target word")
    return clean


def _hash_pick(items: list[str], seed: str) -> str:
    if not items:
        return ""
    total = sum(ord(ch) for ch in seed)
    return items[total % len(items)]


def clean_word_token(word: str) -> str:
    return re.sub(r"[^a-z']+", "", (word or "").strip().lower())


def re_words(text: str) -> set[str]:
    return set(_WORD_RE.findall((text or "").lower()))


def _word_count(text: str) -> int:
    return len(_WORD_RE.findall((text or "").lower()))


def _grammar_score(example: str) -> float:
    text = (example or "").strip()
    if not text:
        return 0.0
    score = 1.0
    if not text[0].isupper():
        score -= 0.1
    if text[-1] not in ".!?":
        score -= 0.15
    if "  " in text:
        score -= 0.1
    if len(re.findall(r"[.!?]", text)) > 1:
        score -= 0.25
    if re.search(r"[\"“”]", text):
        score -= 0.2
    if re.search(r"\b(i am|i'm)\b.*\b(i am|i'm)\b", text.lower()):
        score -= 0.2
    return max(0.0, min(1.0, score))


def _naturalness_score(example: str, word: str) -> float:
    text = re.sub(r"\s+", " ", (example or "").strip().lower())
    if not text:
        return 0.0
    score = 1.0
    weak_fragments = [
        "the morning we",
        "in my daily life",
        "to remember",
        "in a short dialogue",
        "sounded natural",
        "the sentence",
        "the phrase",
        "the word",
        "in context",
        "in today's plan",
        "while planning next month",
    ]
    for fragment in weak_fragments:
        if fragment in text:
            score -= 0.14
    clean_word = clean_word_token(word)
    if clean_word and text.startswith(f"the {clean_word} ") and clean_word.endswith(("ize", "ise", "ify", "ate")):
        score -= 0.35
    if clean_word and f'"{clean_word}"' in text:
        score -= 0.25
    if clean_word and f"'{clean_word}'" in text:
        score -= 0.25
    if clean_word and text.startswith(f"i have to {clean_word} "):
        score -= 0.18
    if re.match(r"^the [a-z']+ of the [a-z']+ is [a-z']+\.$", text):
        score -= 0.22
    if " very " in f" {text} ":
        score -= 0.06
    if "such as" in text:
        score -= 0.08
    if "by learning new words" in text:
        score -= 0.2
    tokens = _WORD_RE.findall(text)
    if tokens:
        diversity = len(set(tokens)) / max(1, len(tokens))
        if diversity < 0.62:
            score -= 0.18
    return max(0.0, min(1.0, score))


def _semantic_match_score(example: str, word: str, meaning_cn: str = "", book_id: str | None = None) -> float:
    text = re.sub(r"\s+", " ", (example or "").strip().lower())
    clean_word = clean_word_token(word)
    if not text or not clean_word:
        return 0.0
    if clean_word not in re_words(text):
        return 0.0
    score = 1.0
    wc = _word_count(text)
    if wc < MIN_WORDS or wc > MAX_WORDS:
        score -= 0.35
    pos = _guess_pos(clean_word, meaning_cn)
    if pos == "verb" and text.startswith(f"the {clean_word} "):
        score -= 0.35
    if pos == "adjective" and clean_word in {"resilient", "efficient", "stable"} and f"the {clean_word} " in text:
        score -= 0.2
    context_keywords: dict[str, set[str]] = {
        "workplace": {"team", "meeting", "report", "client", "project", "manager", "deadline", "plan"},
        "computer_science": {"server", "service", "system", "api", "latency", "deploy", "config", "code"},
        "ielts_academic": {"research", "policy", "evidence", "essay", "society", "education", "public"},
        "toefl_academic": {"research", "lecture", "campus", "lab", "assignment", "students", "professor"},
    }
    if book_id in context_keywords:
        tokens = re_words(text)
        if tokens.isdisjoint(context_keywords[book_id]):
            score -= 0.15
    return max(0.0, min(1.0, score))


def _example_quality_score(
    example: str, word: str, meaning_cn: str = "", book_id: str | None = None
) -> dict[str, float]:
    grammar = _grammar_score(example)
    naturalness = _naturalness_score(example, word)
    semantic = _semantic_match_score(example, word, meaning_cn, book_id)
    total = grammar * 0.34 + naturalness * 0.33 + semantic * 0.33
    return {
        "grammar": round(grammar, 4),
        "naturalness": round(naturalness, 4),
        "semantic": round(semantic, 4),
        "total": round(total, 4),
    }


def _sentence_signature(example: str, word: str) -> str:
    clean_word = clean_word_token(word)
    tokens = _WORD_RE.findall((example or "").lower())
    skeleton = []
    for token in tokens:
        if token == clean_word:
            skeleton.append("{word}")
        elif token in {"i", "we", "they", "he", "she", "it"}:
            skeleton.append("{subj}")
        elif token in {"the", "a", "an"}:
            skeleton.append("{det}")
        else:
            skeleton.append(token)
    return " ".join(skeleton[:10])


def _jaccard_tokens(a: str, b: str) -> float:
    wa = re_words(a)
    wb = re_words(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / max(1, len(wa | wb))


def _is_duplicate_candidate(example: str, word: str, history: dict[str, Any]) -> bool:
    clean = re.sub(r"\s+", " ", (example or "").strip().lower())
    if not clean:
        return True
    recent_examples = [str(x).strip().lower() for x in history.get("recent_examples") or []][-80:]
    recent_signatures = [str(x).strip().lower() for x in history.get("recent_signatures") or []][-80:]
    signature = _sentence_signature(clean, word).lower()
    if clean in recent_examples:
        return True
    if signature and signature in recent_signatures:
        return True
    for prev in recent_examples[-30:]:
        if _jaccard_tokens(clean, prev) >= 0.72:
            return True
    return False


def _remember_example(example: str, word: str, template_id: str | None = None) -> None:
    history = _read_history()
    clean = re.sub(r"\s+", " ", (example or "").strip())
    if clean:
        history.setdefault("recent_examples", []).append(clean)
        history.setdefault("recent_signatures", []).append(_sentence_signature(clean, word))
    if template_id:
        history.setdefault("recent_template_ids", []).append(template_id)
    _write_history(history)


def _guess_pos(word: str, meaning_cn: str = "") -> str:
    clean = clean_word_token(word)
    hint = (meaning_cn or "").strip().lower()
    if clean in SEMANTIC_WORDS.get("action", set()):
        return "verb"
    if "v." in hint or "verb" in hint or "动词" in hint:
        return "verb"
    if "adj." in hint or "adjective" in hint or "形容词" in hint:
        return "adjective"
    if "n." in hint or "noun" in hint or "名词" in hint:
        return "noun"
    if clean.endswith(("ize", "ise", "ify")):
        return "verb"
    if clean.endswith(("ous", "ful", "able", "ible", "ive", "less")):
        return "adjective"
    return "noun"


def _template_pool(book_id: str | None, pos: str) -> list[dict[str, str]]:
    daily = {
        "noun": [
            {"id": "daily_n_1", "text": "The {word} was easy to notice in the room."},
            {"id": "daily_n_2", "text": "The {word} became important later that day."},
            {"id": "daily_n_3", "text": "She asked a clear question about the {word}."},
            {"id": "daily_n_4", "text": "She explained the {word} with a simple example."},
        ],
        "verb": [
            {"id": "daily_v_1", "text": "We should {word} the plan before tomorrow."},
            {"id": "daily_v_2", "text": "I had to {word} quickly before leaving."},
            {"id": "daily_v_3", "text": "Leaders hope to {word} current conditions over time."},
            {"id": "daily_v_4", "text": "We need to {word} the process before the final review."},
        ],
        "adjective": [
            {"id": "daily_a_1", "text": "This option feels {word} for everyday use."},
            {"id": "daily_a_2", "text": "The new routine is more {word} than the old one."},
            {"id": "daily_a_3", "text": "Her suggestion sounds {word} and easy to follow."},
            {"id": "daily_a_4", "text": "That change made the process {word}."},
        ],
        "other": [
            {"id": "daily_o_1", "text": "We paused, and {word} the room became quiet."},
            {"id": "daily_o_2", "text": "I looked around and {word} noticed the sign."},
            {"id": "daily_o_3", "text": "She hesitated, {word} gave her final answer."},
            {"id": "daily_o_4", "text": "He took a breath and {word} continued speaking."},
        ],
    }
    workplace = {
        "noun": [
            {"id": "work_n_1", "text": "The team reviewed the {word} before the client call."},
            {"id": "work_n_2", "text": "Please add the {word} to today's meeting agenda."},
            {"id": "work_n_3", "text": "Finance approved the {word} for next quarter."},
            {"id": "work_n_4", "text": "We included the {word} in the weekly report."},
        ],
        "verb": [
            {"id": "work_v_1", "text": "We need to {word} the proposal before noon."},
            {"id": "work_v_2", "text": "Could you {word} this draft and send feedback?"},
            {"id": "work_v_3", "text": "Let's {word} the timeline after the stand-up."},
            {"id": "work_v_4", "text": "I will {word} the numbers before sharing them."},
        ],
        "adjective": [
            {"id": "work_a_1", "text": "We need a more {word} process for onboarding."},
            {"id": "work_a_2", "text": "The report is {word} enough for leadership review."},
            {"id": "work_a_3", "text": "This timeline looks {word} for both teams."},
            {"id": "work_a_4", "text": "Their response stayed {word} and factual."},
        ],
        "other": [
            {"id": "work_o_1", "text": "In the update, {word} we proposed a safer rollout."},
            {"id": "work_o_2", "text": "The manager agreed, {word} the team moved forward."},
            {"id": "work_o_3", "text": "We adjusted the timeline, {word} reduced delivery risk."},
            {"id": "work_o_4", "text": "The report was clear, {word} leadership approved it quickly."},
        ],
    }
    exam = {
        "noun": [
            {"id": "exam_n_1", "text": "Contemporary research suggests that the {word} shapes social outcomes."},
            {"id": "exam_n_2", "text": "A balanced essay should examine the limits of the {word}."},
            {"id": "exam_n_3", "text": "Public debates increasingly focus on the impact of the {word}."},
            {"id": "exam_n_4", "text": "Historical evidence shows that the {word} changes over time."},
        ],
        "verb": [
            {"id": "exam_v_1", "text": "Scholars often {word} assumptions before drawing conclusions."},
            {"id": "exam_v_2", "text": "Governments should {word} policy goals with long-term equity in mind."},
            {"id": "exam_v_3", "text": "Universities may {word} evidence from multiple disciplines."},
            {"id": "exam_v_4", "text": "A strong argument must {word} both causes and consequences."},
        ],
        "adjective": [
            {"id": "exam_a_1", "text": "The policy appears {word}, yet implementation remains uneven."},
            {"id": "exam_a_2", "text": "This trend is increasingly {word} across major cities."},
            {"id": "exam_a_3", "text": "The evidence is {word} enough to challenge old assumptions."},
            {"id": "exam_a_4", "text": "Such reforms are politically {word} but socially contested."},
        ],
        "other": [
            {"id": "exam_o_1", "text": "In academic writing, {word} can mark a clear contrast."},
            {"id": "exam_o_2", "text": "The paragraph reads better when {word} links two claims."},
            {"id": "exam_o_3", "text": "A high-band response uses {word} with control and precision."},
            {"id": "exam_o_4", "text": "The argument stays coherent because {word} guides the transition."},
        ],
    }
    tech = {
        "noun": [
            {"id": "tech_n_1", "text": "The engineer inspected the {word} before deployment."},
            {"id": "tech_n_2", "text": "Our logs captured the {word} during the outage."},
            {"id": "tech_n_3", "text": "The patch improved the {word} across all services."},
            {"id": "tech_n_4", "text": "We documented the {word} in yesterday's benchmark report."},
        ],
        "verb": [
            {"id": "tech_v_1", "text": "We need to {word} the service before release."},
            {"id": "tech_v_2", "text": "The script can {word} each file automatically."},
            {"id": "tech_v_3", "text": "They {word} requests in batches to reduce latency."},
            {"id": "tech_v_4", "text": "Please {word} the config and restart the worker."},
        ],
        "adjective": [
            {"id": "tech_a_1", "text": "The new implementation is {word} and easier to maintain."},
            {"id": "tech_a_2", "text": "This endpoint stays {word} under heavy traffic."},
            {"id": "tech_a_3", "text": "The rollout plan looks {word} for production."},
            {"id": "tech_a_4", "text": "Their fix is {word}, but we still need regression tests."},
        ],
        "other": [
            {"id": "tech_o_1", "text": "In the design doc, {word} clarifies how modules interact."},
            {"id": "tech_o_2", "text": "The runbook became clearer once {word} connected two steps."},
            {"id": "tech_o_3", "text": "During incident review, {word} explained the failure chain."},
            {"id": "tech_o_4", "text": "In the postmortem, {word} made the root cause easier to follow."},
        ],
    }
    selected_pos = pos if pos in {"noun", "verb", "adjective", "other"} else "noun"
    if book_id == "workplace":
        return workplace[selected_pos]
    if book_id == "computer_science":
        return tech[selected_pos]
    if book_id in {"ielts_academic", "toefl_academic"}:
        return exam[selected_pos]
    return daily[selected_pos]


def _all_templates(book_id: str | None) -> list[dict[str, str]]:
    merged: list[dict[str, str]] = []
    for pos in ("noun", "verb", "adjective", "other"):
        merged.extend(_template_pool(book_id, pos))
    return merged


def _pick_best_template_candidate(
    word: str, book_id: str | None, meaning_cn: str, history: dict[str, Any]
) -> tuple[str, str, dict[str, float]]:
    clean_word = clean_word_token(word) or "topic"
    guessed_pos = _guess_pos(clean_word, meaning_cn)
    templates = _template_pool(book_id, guessed_pos)
    if not templates:
        templates = [{"id": "fallback_default", "text": "People discussed the {word} during lunch."}]
    best_sentence = ""
    best_template_id = templates[0]["id"]
    best_score = {"grammar": 0.0, "naturalness": 0.0, "semantic": 0.0, "total": 0.0}
    for candidate in templates:
        maybe = _clean_sentence(candidate["text"].format(word=clean_word), clean_word)
        if _is_duplicate_candidate(maybe, clean_word, history):
            continue
        quality = _example_quality_score(maybe, clean_word, meaning_cn, book_id)
        if quality["total"] > best_score["total"]:
            best_sentence = maybe
            best_template_id = candidate["id"]
            best_score = quality
    if not best_sentence:
        fallback = templates[0]
        best_sentence = _clean_sentence(fallback["text"].format(word=clean_word), clean_word)
        best_template_id = fallback["id"]
        best_score = _example_quality_score(best_sentence, clean_word, meaning_cn, book_id)
    return best_sentence, best_template_id, best_score


def _scene_fallback_example(word: str, book_id: str | None, meaning_cn: str = "") -> tuple[str, str]:
    history = _read_history()
    sentence, template_id, _quality = _pick_best_template_candidate(word, book_id, meaning_cn, history)
    return sentence, template_id


def _meaning_head(meaning_cn: str, word: str) -> str:
    text = re.split(r"[;；，,。]", str(meaning_cn or "").strip(), maxsplit=1)[0].strip()
    text = re.sub(r"^[a-zA-Z']+\s*[:：]\s*", "", text).strip()
    return text or clean_word_token(word) or "这个词"


SEMANTIC_WORDS: dict[str, set[str]] = {
    "transport": {"airport", "bus", "car", "flight", "plane", "station", "subway", "taxi", "train"},
    "action": {
        "call",
        "clean",
        "cook",
        "drive",
        "learn",
        "listen",
        "read",
        "run",
        "shop",
        "study",
        "talk",
        "travel",
        "wait",
        "walk",
        "work",
        "write",
    },
    "place": {
        "bank",
        "beach",
        "building",
        "campus",
        "city",
        "classroom",
        "country",
        "hotel",
        "hospital",
        "kitchen",
        "lab",
        "laboratory",
        "library",
        "market",
        "museum",
        "office",
        "park",
        "restaurant",
        "river",
        "road",
        "room",
        "school",
        "store",
        "street",
        "village",
    },
    "food": {
        "breakfast",
        "bread",
        "chicken",
        "coffee",
        "dinner",
        "egg",
        "fruit",
        "lunch",
        "meal",
        "milk",
        "oil",
        "rice",
        "tea",
        "vegetable",
        "water",
    },
    "portable_object": {
        "bag",
        "book",
        "charger",
        "computer",
        "key",
        "keys",
        "laptop",
        "medicine",
        "package",
        "phone",
        "receipt",
        "ticket",
        "umbrella",
        "wallet",
    },
    "work_item": {
        "agenda",
        "budget",
        "deadline",
        "document",
        "feedback",
        "invoice",
        "meeting",
        "message",
        "milestone",
        "plan",
        "priority",
        "project",
        "proposal",
        "report",
        "schedule",
        "task",
        "workflow",
    },
    "tech": {
        "algorithm",
        "api",
        "backend",
        "cache",
        "config",
        "database",
        "deployment",
        "endpoint",
        "frontend",
        "latency",
        "pipeline",
        "repository",
        "rollback",
        "server",
        "service",
        "system",
    },
    "time": {"afternoon", "evening", "friday", "morning", "night", "sunday", "weekend"},
    "abstract": {"choice", "conclusion", "discussion", "idea", "issue", "reason", "research", "risk", "weather"},
}


def _semantic_category(word: str, meaning_cn: str = "", book_id: str | None = None) -> str:
    clean = clean_word_token(word)
    for category, words in SEMANTIC_WORDS.items():
        if clean in words:
            return category
    hint = f"{meaning_cn or ''} {book_id or ''}".lower()
    if book_id == "computer_science" or re.search(r"api|cache|server|database|deployment|latency|software|system", hint):
        return "tech"
    if book_id == "workplace" or re.search(r"meeting|project|report|budget|agenda|schedule|deadline", hint):
        return "work_item"
    if re.search(r"[旅馆酒店客栈机场车站学校医院银行办公室公园餐厅商店市场图书馆教室城市村庄]", meaning_cn or ""):
        return "place"
    if re.search(r"[早餐午餐晚餐面包鸡肉鸡蛋咖啡茶水米饭水果蔬菜食物餐]", meaning_cn or ""):
        return "food"
    if re.search(r"[消息报告计划预算议程日程截止反馈项目任务文档发票]", meaning_cn or ""):
        return "work_item"
    if re.search(r"[手机电脑书钥匙包票据收据雨伞钱包药]", meaning_cn or ""):
        return "portable_object"
    if re.search(r"[早晨上午下午晚上周末星期夜晚]", meaning_cn or ""):
        return "time"
    return "generic"


def _pick_semantic_scene(
    options: list[tuple[str, str, str]], word: str, book_id: str | None, category: str
) -> tuple[str, str, str]:
    history = _read_history()
    recent_ids = set(str(x) for x in (history.get("recent_template_ids") or [])[-30:])
    if not options:
        return f"I noticed the {word} in a useful sentence.", f"我在一个实用句子里注意到了{_meaning_head('', word)}。", "semantic_empty"
    start = sum(ord(ch) for ch in f"{book_id}:{word}:{category}:semantic") % len(options)
    for offset in range(len(options)):
        candidate = options[(start + offset) % len(options)]
        if candidate[2] not in recent_ids:
            return candidate
    return options[start]


def _semantic_example_pair(word: str, book_id: str | None, meaning_cn: str) -> tuple[str, str, str] | None:
    clean = clean_word_token(word)
    meaning = _meaning_head(meaning_cn, clean)
    category = _semantic_category(clean, meaning, book_id)

    specific_scenes: dict[str, list[tuple[str, str, str]]] = {
        "walk": [
            ("I walk to the office when the weather is good.", "天气好的时候，我走路去办公室。", "scene_walk_office"),
            ("After dinner, we walk slowly through the park.", "晚饭后，我们在公园里慢慢散步。", "scene_walk_park"),
            ("She decided to walk home instead of taking a taxi.", "她决定走路回家，而不是打车。", "scene_walk_taxi"),
            ("The doctor told him to walk more every day.", "医生告诉他每天多走路。", "scene_walk_doctor"),
        ],
        "airport": [
            ("We arrived at the airport before sunrise.", "我们在日出前到达了机场。", "scene_airport_arrival"),
            ("The airport security line moved faster than expected.", "机场安检队伍比预想中前进得更快。", "scene_airport_security"),
            ("She checked the gate number at the airport.", "她在机场查看了登机口号码。", "scene_airport_gate"),
            ("After the delay, families slept beside their bags at the airport.", "航班延误后，一些家庭在机场里靠着行李睡着了。", "scene_airport_delay_family"),
            ("Because the airport was busy, we printed our boarding passes at home.", "因为机场很忙，我们在家先打印了登机牌。", "scene_airport_boarding_pass"),
        ],
        "bus": [
            ("The bus arrived just as it started to rain.", "刚开始下雨时，公交车正好到了。", "scene_bus_rain"),
            ("I tapped my card when I got on the bus.", "我上公交车时刷了卡。", "scene_bus_card"),
            ("The last bus left ten minutes ago.", "最后一班公交车十分钟前开走了。", "scene_bus_last"),
            ("A student gave up his seat when the bus became crowded.", "公交车变得拥挤时，一名学生让出了座位。", "scene_bus_seat"),
            ("If the bus is late again, I will walk to the office.", "如果公交车又晚点，我就走路去办公室。", "scene_bus_late"),
        ],
        "hotel": [
            ("We stayed at the hotel during the trip.", "我们旅行时住在这家旅馆。", "scene_hotel_stay"),
            ("The hotel receptionist gave us two room keys.", "酒店前台给了我们两张房卡。", "scene_hotel_keys"),
            ("Their hotel room overlooked the river.", "他们的酒店房间可以俯瞰河面。", "scene_hotel_room"),
            ("The hotel lobby smelled of coffee and fresh flowers.", "酒店大堂里有咖啡和鲜花的气味。", "scene_hotel_lobby"),
            ("Although the hotel was small, the staff remembered every guest's name.", "虽然这家酒店不大，员工却记得每位客人的名字。", "scene_hotel_staff"),
        ],
        "station": [
            ("The station platform was crowded during rush hour.", "高峰期车站站台很拥挤。", "scene_station_platform"),
            ("An announcement at the station changed our train time.", "车站的一条广播改了我们的列车时间。", "scene_station_announcement"),
            ("She waited by the station entrance with her suitcase.", "她拖着行李箱在车站入口等候。", "scene_station_entrance"),
            ("The station clock was five minutes ahead of my phone.", "车站时钟比我的手机快了五分钟。", "scene_station_clock"),
            ("When the storm ended, people returned to the station quietly.", "暴风雨结束后，人们安静地回到车站。", "scene_station_storm"),
        ],
    }
    if clean in specific_scenes:
        return _pick_semantic_scene(specific_scenes[clean], clean, book_id, category)

    category_scenes: dict[str, list[tuple[str, str, str]]] = {
        "action": [
            (f"I usually {clean} for ten minutes after dinner.", f"我通常晚饭后{meaning}十分钟。", "semantic_action_after_dinner"),
            (f"She likes to {clean} when the weather is calm.", f"天气平静的时候，她喜欢{meaning}。", "semantic_action_weather"),
            (f"We decided to {clean} before the day became too busy.", f"我们决定在今天变得太忙之前先{meaning}。", "semantic_action_before_busy"),
            (f"He stopped for a moment, then continued to {clean}.", f"他停了一会儿，然后继续{meaning}。", "semantic_action_continue"),
            (f"On quiet weekends, they often {clean} together.", f"安静的周末，他们经常一起{meaning}。", "semantic_action_weekend"),
        ],
        "transport": [
            (f"The {clean} arrived earlier than the timetable showed.", f"{meaning}比时刻表上显示的时间更早到了。", "semantic_transport_timetable"),
            (f"She checked the {clean} schedule before leaving home.", f"她出门前查了{meaning}的时刻表。", "semantic_transport_schedule"),
            (f"Heavy rain delayed the {clean} for several minutes.", f"大雨让{meaning}延误了几分钟。", "semantic_transport_delay"),
            (f"I changed my route because the {clean} was too crowded.", f"因为{meaning}太拥挤，我改了路线。", "semantic_transport_route"),
            (f"By the time we reached the {clean}, the queue had already doubled.", f"我们到达{meaning}时，队伍已经变成了两倍长。", "semantic_transport_queue"),
        ],
        "place": [
            (f"The {clean} opened early on Monday morning.", f"{meaning}周一早上很早就开门了。", "semantic_place_open"),
            (f"We stopped by the {clean} on our way home.", f"我们回家路上顺路去了{meaning}。", "semantic_place_stopby"),
            (f"The {clean} was quiet before the evening crowd arrived.", f"晚高峰人群到来前，{meaning}很安静。", "semantic_place_quiet"),
            (f"A handwritten sign on the {clean} door explained the new hours.", f"{meaning}门上一张手写告示说明了新营业时间。", "semantic_place_sign"),
            (f"Although the {clean} looked small from outside, it was bright and busy inside.", f"虽然{meaning}从外面看不大，里面却明亮又忙碌。", "semantic_place_contrast"),
        ],
        "food": [
            (f"She bought {clean} from a small shop nearby.", f"她在附近的小店买了{meaning}。", "semantic_food_shop"),
            (f"The fresh {clean} smelled warm from the oven.", f"新鲜的{meaning}带着刚出炉的热香。", "semantic_food_oven"),
            (f"He saved some {clean} for tomorrow's breakfast.", f"他留了一些{meaning}给明天早餐。", "semantic_food_breakfast"),
            (f"The children shared the {clean} before the picnic began.", f"野餐开始前，孩子们分享了{meaning}。", "semantic_food_picnic"),
            (f"Because the {clean} was still hot, she wrapped it in a napkin.", f"因为{meaning}还很热，她用餐巾把它包了起来。", "semantic_food_hot"),
        ],
        "portable_object": [
            (f"I put the {clean} beside my laptop.", f"我把{meaning}放在笔记本电脑旁边。", "semantic_object_desk"),
            (f"She found the {clean} at the bottom of her bag.", f"她在包底找到了{meaning}。", "semantic_object_bag"),
            (f"Please bring the {clean} to the front desk.", f"请把{meaning}带到前台。", "semantic_object_frontdesk"),
            (f"He kept the {clean} in his coat pocket all day.", f"他一整天都把{meaning}放在外套口袋里。", "semantic_object_pocket"),
            (f"After checking the label, she handed the {clean} to the driver.", f"检查标签后，她把{meaning}交给了司机。", "semantic_object_label"),
        ],
        "work_item": [
            (f"The team reviewed the {clean} before the meeting.", f"团队在会前复盘了{meaning}。", "semantic_work_review"),
            (f"She updated the {clean} after the client call.", f"她在客户电话后更新了{meaning}。", "semantic_work_update"),
            (f"The manager highlighted the {clean} in the weekly report.", f"经理在周报中强调了{meaning}。", "semantic_work_report"),
            (f"Before anyone made a decision, the {clean} was pinned to the screen.", f"在任何人做决定之前，{meaning}被固定在屏幕上。", "semantic_work_screen"),
            (f"Although the {clean} looked simple, it changed the team's priorities.", f"虽然{meaning}看起来简单，它却改变了团队的优先级。", "semantic_work_priority"),
        ],
        "tech": [
            (f"The engineer checked the {clean} before deployment.", f"工程师在部署前检查了{meaning}。", "semantic_tech_check"),
            (f"The logs showed a problem with the {clean}.", f"日志显示{meaning}出现了问题。", "semantic_tech_logs"),
            (f"They optimized the {clean} before the release.", f"他们在发布前优化了{meaning}。", "semantic_tech_optimize"),
            (f"Once the {clean} warmed up, the dashboard loaded almost instantly.", f"{meaning}预热完成后，仪表盘几乎立刻加载完成。", "semantic_tech_warmup"),
            (f"If the {clean} fails again, the worker will switch to a backup path.", f"如果{meaning}再次失败，worker 会切到备用路径。", "semantic_tech_backup"),
        ],
        "time": [
            (f"The {clean} was quiet and cool.", f"{meaning}很安静，也很凉爽。", "semantic_time_cool"),
            (f"I saved this task for the {clean}.", f"我把这个任务留到{meaning}处理。", "semantic_time_task"),
            (f"The {clean} felt shorter than usual.", f"{meaning}感觉比平时更短。", "semantic_time_short"),
            (f"By the {clean}, everyone had already heard the news.", f"到了{meaning}，大家已经听说了这个消息。", "semantic_time_news"),
            (f"She prefers studying in the {clean}, when the house is quiet.", f"她喜欢在{meaning}学习，因为那时家里很安静。", "semantic_time_study"),
        ],
        "abstract": [
            (f"The discussion focused on the {clean}.", f"讨论集中在{meaning}上。", "semantic_abstract_discussion"),
            (f"Her answer changed our view of the {clean}.", f"她的回答改变了我们对{meaning}的看法。", "semantic_abstract_view"),
            (f"The report explains the {clean} with clear examples.", f"报告用清晰例子解释了{meaning}。", "semantic_abstract_report"),
            (f"Without more evidence, the {clean} remained difficult to judge.", f"没有更多证据，{meaning}仍然很难判断。", "semantic_abstract_evidence"),
            (f"The {clean} sounded simple until we tried to explain it to others.", f"{meaning}听起来简单，直到我们试着向别人解释它。", "semantic_abstract_explain"),
        ],
    }
    options = category_scenes.get(category)
    if options:
        return _pick_semantic_scene(options, clean, book_id, category)
    return None


def _is_semantically_bad_example(example: str, word: str, meaning_cn: str = "") -> bool:
    text = re.sub(r"\s+", " ", (example or "").strip().lower())
    clean = clean_word_token(word)
    if not text or not clean:
        return False
    category = _semantic_category(clean, meaning_cn)
    escaped = re.escape(clean)
    if category == "place" and (
        re.search(rf"\b(left|put|packed|grabbed|bought|cooked|ate|drank)\b[^.?!]*\b{escaped}\b", text)
        or re.search(rf"\b{escaped}\b[^.?!]*\b(on|in) the kitchen table\b", text)
        or f"discount on {clean}" in text
        or f"{clean} was on sale" in text
    ):
        return True
    if category != "food" and (
        re.search(rf"\b(cooked|ate|drank|ordered)\b[^.?!]*\b{escaped}\b", text)
        or re.search(rf"\b{escaped}\b[^.?!]*\b(after dinner|for breakfast)\b", text)
    ):
        return True
    if category != "portable_object" and re.search(
        rf"\b(grabbed|packed|left|put)\b[^.?!]*\b{escaped}\b[^.?!]*\b(on the table|in the bag|before getting on the bus)\b",
        text,
    ):
        return True
    if category in {"action", "abstract"} and (
        re.search(rf"\b(saw|noticed|enjoyed)\b\s+the\s+{escaped}\b", text)
        or re.search(rf"\btalked about\s+the\s+{escaped}\b", text)
        or re.search(rf"\bthe\s+{escaped}\b\s+on my way home\b", text)
    ):
        return True
    return False


def _template_translation(template_id: str, word: str, meaning_cn: str) -> str:
    meaning = _meaning_head(meaning_cn, word)
    templates = {
        "daily_n_1": f"在房间里，{meaning}很容易被注意到。",
        "daily_n_2": f"{meaning}在那天晚些时候变得很重要。",
        "daily_n_3": f"他们在车站附近看到了{meaning}。",
        "daily_n_4": f"她用一个简单的例子解释了{meaning}。",
        "daily_v_1": f"我们应该在明天之前处理好{meaning}这件事。",
        "daily_v_2": f"我离开前不得不很快完成{meaning}这个动作。",
        "daily_v_3": f"人们希望随着时间改善当前状况。",
        "daily_v_4": f"最终复盘前，我们需要处理好这个流程。",
        "daily_a_1": f"这个选项在日常使用中显得比较{meaning}。",
        "daily_a_2": f"新的日常安排比旧的更{meaning}。",
        "daily_a_3": f"她的建议听起来{meaning}，也容易照做。",
        "daily_a_4": f"这个改变让流程变得更{meaning}。",
        "work_n_1": f"团队在客户电话前复盘了{meaning}。",
        "work_n_2": f"请把{meaning}加到今天的会议议程里。",
        "work_n_3": f"财务批准了下个季度的{meaning}。",
        "work_n_4": f"我们把{meaning}写进了周报。",
        "tech_n_1": f"工程师在部署前检查了{meaning}。",
        "tech_n_2": f"日志在故障期间记录了{meaning}。",
        "tech_n_3": f"这个补丁改善了所有服务中的{meaning}。",
        "tech_n_4": f"我们在昨天的基准报告中记录了{meaning}。",
    }
    if template_id in templates:
        return templates[template_id]
    if template_id.startswith("work_"):
        return f"团队在工作场景中使用了{meaning}。"
    if template_id.startswith("tech_"):
        return f"工程师在技术场景中使用了{meaning}。"
    if template_id.startswith("exam_"):
        return f"学术写作中可以用{meaning}来表达观点。"
    return f"这个句子展示了{meaning}的自然用法。"


def _specific_example_pair(word: str, book_id: str | None, meaning_cn: str) -> tuple[str, str, str] | None:
    clean = clean_word_token(word)
    meaning = _meaning_head(meaning_cn, clean)
    daily: dict[str, tuple[str, str]] = {
        "coffee": ("He ordered coffee before the morning meeting.", "他在早会前点了一杯咖啡。"),
        "tea": ("She made tea after dinner.", "她晚饭后泡了茶。"),
        "water": ("Please drink more water after exercise.", "运动后请多喝水。"),
        "oil": ("She used a little oil to cook dinner.", "她做晚饭时用了一点油。"),
        "river": ("They walked along the river after lunch.", "他们午饭后沿着河边散步。"),
        "airport": ("The airport security line moved faster than expected.", "机场安检队伍比预想中前进得更快。"),
        "bus": ("The bus arrived just as it started to rain.", "刚开始下雨时，公交车正好到了。"),
        "station": ("An announcement at the station changed our train time.", "车站的一条广播改了我们的列车时间。"),
        "office": ("She left her laptop at the office.", "她把笔记本电脑落在办公室了。"),
        "bank": ("I went to the bank after work.", "我下班后去了银行。"),
        "hotel": ("The hotel receptionist gave us two room keys.", "酒店前台给了我们两张房卡。"),
        "school": ("The school opened its library early today.", "学校今天很早开放了图书馆。"),
        "hospital": ("The hospital called to confirm her appointment.", "医院打电话确认了她的预约。"),
        "market": ("We bought fresh fruit at the market.", "我们在市场买了新鲜水果。"),
        "music": ("She listened to music while cooking dinner.", "她做晚饭时听着音乐。"),
        "store": ("The store closes at nine tonight.", "这家商店今晚九点关门。"),
        "road": ("The road was quiet after the rain.", "雨后这条路很安静。"),
        "street": ("The street lights turned on at sunset.", "日落时街灯亮了起来。"),
        "phone": ("My phone buzzed twice while the meeting room was silent.", "会议室安静时，我的手机震动了两次。"),
        "computer": ("This computer runs the local service.", "这台电脑运行本地服务。"),
        "message": ("I sent her a message after the meeting.", "会议后我给她发了一条消息。"),
        "schedule": ("Her schedule is full this afternoon.", "她今天下午的日程很满。"),
        "budget": ("Although the budget looked simple, it changed the team's priorities.", "虽然预算看起来简单，它却改变了团队的优先级。"),
    }
    workplace: dict[str, tuple[str, str]] = {
        "agenda": ("Please share the agenda before the meeting.", "请在会议前分享议程。"),
        "deadline": ("The deadline moved to Friday afternoon.", "截止时间改到了周五下午。"),
        "feedback": ("Her feedback helped us improve the design.", "她的反馈帮助我们改进了设计。"),
        "proposal": ("The manager reviewed the proposal before noon.", "经理在中午前审阅了提案。"),
        "report": ("He finished the weekly report this morning.", "他今天早上完成了周报。"),
        "project": ("The project needs a clear timeline.", "这个项目需要清晰的时间表。"),
    }
    tech: dict[str, tuple[str, str]] = {
        "cache": ("Once the cache warmed up, the dashboard loaded almost instantly.", "缓存预热完成后，仪表盘几乎立刻加载完成。"),
        "server": ("The server restarted after the update.", "服务器在更新后重启了。"),
        "database": ("The database stored the user settings.", "数据库保存了用户设置。"),
        "deployment": ("The deployment finished before midnight.", "部署在午夜前完成了。"),
        "latency": ("The patch reduced latency for every request.", "这个补丁降低了每次请求的延迟。"),
    }
    pools = [daily]
    if book_id == "workplace":
        pools.insert(0, workplace)
    if book_id == "computer_science":
        pools.insert(0, tech)
    for pool in pools:
        if clean in pool:
            example, example_cn = pool[clean]
            return example, example_cn, f"specific_{clean}"
    semantic = _semantic_example_pair(clean, book_id, meaning_cn)
    if semantic:
        return semantic
    if clean.endswith("ing") and len(clean) > 5:
        return (
            f"She practiced {clean} for a few minutes after work.",
            f"她下班后练习了一会儿{meaning}。",
            "specific_ing",
        )
    return None


def _looks_like_template_fallback(example: str, word: str) -> bool:
    text = re.sub(r"\s+", " ", (example or "").strip().lower())
    clean_word = (word or "").strip().lower()
    if _is_semantically_bad_example(text, clean_word):
        return True
    generic_fragments = [
        "while preparing for the day",
        "while walking home",
        "at home last night",
        "while making a simple plan",
        "during their weekend errands",
        "came up in a normal conversation",
        "we met near the",
        "had a discount on",
        "was on sale",
        "want to remember",
        "learn the word",
        "while preparing dinner",
        "clear example for",
        "will be refreshed locally",
        "in a short dialogue",
        "sounded natural",
        "with me now",
    ]
    if any(fragment in text for fragment in generic_fragments):
        return True
    pool = _all_templates("daily_life_ngsl") + _all_templates("workplace") + _all_templates("computer_science")
    for item in pool:
        maybe = item["text"].format(word=clean_word).lower().strip()
        if maybe == text:
            return True
    return False


def _looks_like_old_fast_template(example: str) -> bool:
    text = re.sub(r"\s+", " ", (example or "").strip().lower())
    old_fragments = [
        "people discussed the",
        "the article mentioned the",
        "she noticed the",
        "we included the",
        "plays an important role in daily life",
        "we met near the",
        "had a discount on",
        "was on sale",
        "on the kitchen table",
        "before getting on the bus",
        "packed the",
        "we should ",
        "this option feels ",
        "put the",
    ]
    return any(fragment in text for fragment in old_fragments)


def _is_bad_example(example: str, word: str, history: dict[str, Any]) -> bool:
    text = re.sub(r"\s+", " ", (example or "").strip())
    if not text:
        return True
    if _is_semantically_bad_example(text, word):
        return True
    lowered = text.lower()
    if _looks_like_template_fallback(lowered, word):
        return True
    wc = _word_count(lowered)
    if wc < MIN_WORDS or wc > MAX_WORDS:
        return True
    if clean_word_token(word) not in re_words(lowered):
        return True
    if len(re.findall(r"[.!?]", text)) > 1:
        return True
    clean_word = clean_word_token(word)
    if clean_word and lowered.startswith(f"{clean_word} means"):
        return True
    if clean_word and f'"{clean_word}"' in lowered:
        return True
    if clean_word and f"'{clean_word}'" in lowered:
        return True
    if clean_word and lowered.startswith(f"the {clean_word} ") and clean_word.endswith(("ize", "ise", "ify", "ate")):
        return True
    if "in a certain period of time" in lowered:
        return True
    if "in my daily life" in lowered:
        return True
    if "by learning new words" in lowered:
        return True
    if _is_duplicate_candidate(lowered, word, history):
        return True
    return False


def english_example_model_status() -> dict[str, Any]:
    model_path = _model_path()
    cuda_dll_dirs = _add_cuda_dll_dirs()
    try:
        import llama_cpp  # noqa: F401
        from llama_cpp import llama_cpp as llama_low

        runtime_ready = True
        runtime_error = None
        gpu_offload_supported = bool(
            getattr(llama_low, "llama_supports_gpu_offload", lambda: False)()
        )
    except Exception as exc:
        runtime_ready = False
        runtime_error = str(exc)
        gpu_offload_supported = False
    return {
        "ok": True,
        "model_id": MODEL_ID,
        "model_path": str(model_path),
        "model_installed": model_path.is_file(),
        "runtime_ready": runtime_ready,
        "runtime_error": runtime_error,
        "gpu_offload_supported": gpu_offload_supported,
        "cuda_dll_dirs": cuda_dll_dirs,
        "n_gpu_layers": _env_int(
            "JACHIN_EXAMPLE_LLM_GPU_LAYERS",
            -1 if gpu_offload_supported else 0,
        ),
        "fallback_available": True,
    }


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(str(raw).strip())
    except Exception:
        return default


@lru_cache(maxsize=1)
def _llm():
    _add_cuda_dll_dirs()
    from llama_cpp import Llama
    from llama_cpp import llama_cpp as llama_low

    model_path = _model_path()
    if not model_path.is_file():
        raise RuntimeError(f"Local example model is not installed: {model_path}")
    gpu_offload_supported = bool(
        getattr(llama_low, "llama_supports_gpu_offload", lambda: False)()
    )
    return Llama(
        model_path=str(model_path),
        n_ctx=_env_int("JACHIN_EXAMPLE_LLM_CTX", 768),
        n_threads=_env_int("JACHIN_EXAMPLE_LLM_THREADS", max(2, min(8, os.cpu_count() or 4))),
        n_gpu_layers=_env_int(
            "JACHIN_EXAMPLE_LLM_GPU_LAYERS",
            -1 if gpu_offload_supported else 0,
        ),
        verbose=False,
    )


def _template_draft(clean_word: str, book_id: str | None, meaning_cn: str) -> tuple[str, str, str]:
    specific = _specific_example_pair(clean_word, book_id, meaning_cn)
    if specific:
        return specific
    example, template_id = _scene_fallback_example(clean_word, book_id, meaning_cn)
    example_cn = _template_translation(template_id, clean_word, meaning_cn)
    return example, example_cn, template_id


def _llm_review_or_rewrite_template(
    draft: str,
    clean_word: str,
    book_id: str | None,
    meaning_cn: str,
    history: dict[str, Any],
) -> tuple[str, dict[str, float]] | None:
    if not draft:
        return None
    prompt = (
        "<|im_start|>system\n"
        "You are an English vocabulary example reviewer. Return strict JSON only.\n"
        "<|im_end|>\n"
        "<|im_start|>user\n"
        f"Target word: {clean_word}\n"
        f"Chinese meaning hint: {meaning_cn}\n"
        f"Scene: {_scene(book_id)}\n"
        f"Draft sentence: {draft}\n"
        "Task: If the draft is natural and semantically correct, keep it. "
        "If it is awkward, illogical, template-like, or uses the word incorrectly, rewrite it.\n"
        "Rules: one natural English sentence, 6-18 words, include the exact target word, no meta wording.\n"
        "Return JSON: {\"ok\":true,\"example\":\"...\",\"reason\":\"...\"}\n"
        "<|im_end|>\n"
        "<|im_start|>assistant\n"
    )
    try:
        result = _llm()(
            prompt,
            max_tokens=110,
            temperature=0.28,
            top_p=0.88,
            repeat_penalty=1.1,
            stop=["<|im_end|>", "\n\n"],
        )
        raw = result["choices"][0]["text"]
        parsed = _extract_json(raw)
        candidate = _clean_sentence(str(parsed.get("example") or draft), clean_word)
    except Exception:
        return None
    if _is_bad_example(candidate, clean_word, history):
        return None
    quality = _example_quality_score(candidate, clean_word, meaning_cn, book_id)
    if quality["total"] < QUALITY_REGEN_SCORE:
        return None
    return candidate, quality


def english_generate_example_card(word: str, book_id: str = "daily_life_ngsl", meaning_cn: str = "") -> dict[str, Any]:
    clean_word = (word or "").strip().lower()
    if not clean_word:
        return {"ok": False, "error": "word is empty"}
    key = _cache_key(clean_word, book_id)
    cache = _read_cache()
    status = english_example_model_status()
    model_ready = bool(status.get("model_installed") and status.get("runtime_ready"))
    cached = cache.get(key)
    if isinstance(cached, dict) and cached.get("example"):
        cached_model = str(cached.get("model_id") or "")
        cached_source = str(cached.get("source") or "")
        cached_example = str(cached.get("example") or "")
        cached_example_cn = str(cached.get("example_cn") or "").strip()
        cached_is_template = (
            cached_model.startswith("local_scene_templates_")
            or cached_model == TEMPLATE_ENGINE_ID
            or cached_source.startswith("local_scene")
            or cached_model.startswith("local_fast_examples_")
            or cached_source.startswith("local_fast")
        )
        if cached_is_template and model_ready:
            # Template fallback entries are not product-grade examples. Drop them
            # once the GGUF reviewer/generator is available, so stale repeated
            # placeholder sentences do not survive in cache.
            cache.pop(key, None)
            _write_cache(cache)
            cached = None
        if cached is None:
            pass
        invalidate_template_engine = cached_model.startswith("local_scene_templates_") and cached_model != TEMPLATE_ENGINE_ID
        cached_template_id = str(cached.get("template_id") or "") if cached is not None else ""
        if cached is not None and (
            cached_model == "local_scene_generator"
            or invalidate_template_engine
            or (cached_model.startswith("local_fast_examples_") and cached_model != FAST_TEMPLATE_ENGINE_ID)
            or (
                cached_model == FAST_TEMPLATE_ENGINE_ID
                and not cached_template_id.startswith("specific_")
                and _looks_like_old_fast_template(cached_example)
            )
            or (
                cached_model not in {TEMPLATE_ENGINE_ID, FAST_TEMPLATE_ENGINE_ID}
                and _looks_like_template_fallback(cached_example, clean_word)
            )
            or not cached_example_cn
            or "??" in cached_example_cn
            or re.search(rf"\b{re.escape(clean_word)}\b", cached_example_cn.lower())
            or _is_semantically_bad_example(cached_example, clean_word, meaning_cn)
        ):
            cache.pop(key, None)
            _write_cache(cache)
        elif cached is not None:
            cache_source = "local_scene_cache" if cached_is_template else "local_gguf_cache"
            quality = cached.get("quality")
            if not isinstance(quality, dict):
                quality = _example_quality_score(str(cached.get("example") or ""), clean_word, meaning_cn, book_id)
            return {
                "ok": True,
                "word": clean_word,
                "book_id": book_id,
                "example": str(cached.get("example")),
                "example_cn": str(cached.get("example_cn") or ""),
                "model_id": str(cached.get("model_id") or MODEL_ID),
                "source": cache_source,
                "quality": quality,
            }

    history = _read_history()
    model_first = str(os.environ.get("JACHIN_ENGLISH_EXAMPLE_MODEL_FIRST") or MODEL_FIRST_DEFAULT).strip().lower() not in {
        "0",
        "false",
        "off",
        "no",
    }
    fast_first = str(os.environ.get("JACHIN_ENGLISH_EXAMPLE_FAST_FIRST") or "0").strip().lower() not in {
        "0",
        "false",
        "off",
        "no",
    }
    if fast_first and not model_first:
        example, example_cn, template_id = _template_draft(clean_word, book_id, meaning_cn)
        quality = _example_quality_score(example, clean_word, meaning_cn, book_id)
        cache[key] = {
            "example": example,
            "example_cn": example_cn,
            "model_id": FAST_TEMPLATE_ENGINE_ID,
            "source": "local_fast_template",
            "template_id": template_id,
            "quality": quality,
        }
        _write_cache(cache)
        _remember_example(example, clean_word, template_id=template_id or None)
        return {
            "ok": True,
            "word": clean_word,
            "book_id": book_id,
            "example": example,
            "example_cn": example_cn,
            "model_id": FAST_TEMPLATE_ENGINE_ID,
            "source": "local_fast_template",
            "template_id": template_id,
            "runtime_ready": None,
            "quality": quality,
        }

    example = ""
    source = "local_gguf"
    model_id = MODEL_ID
    template_id = ""
    quality = {"grammar": 0.0, "naturalness": 0.0, "semantic": 0.0, "total": 0.0}
    if model_ready:
        avoid_starts = []
        for sentence in (history.get("recent_examples") or [])[-24:]:
            tokens = _WORD_RE.findall(str(sentence).lower())
            if len(tokens) >= 2:
                avoid_starts.append(" ".join(tokens[:2]))
        avoid_text = ", ".join(dict.fromkeys(avoid_starts)) or "none"
        feedback = "none"
        best_llm_candidate = ""
        best_llm_score = dict(quality)
        for _round_idx in range(MAX_LLM_ROUNDS):
            prompt = (
                "<|im_start|>system\n"
                "You create natural English vocabulary examples. Return strict JSON only.\n"
                "<|im_end|>\n"
                "<|im_start|>user\n"
                f"Word: {clean_word}\n"
                f"Chinese meaning hint: {meaning_cn}\n"
                f"Scene: {_scene(book_id)}\n"
                f"Avoid sentence starts: {avoid_text}\n"
                f"Last attempt feedback: {feedback}\n"
                "Rules:\n"
                "- one sentence only\n"
                "- 6-18 words\n"
                "- must include the exact word\n"
                "- natural usage, no memorization phrasing\n"
                "Return JSON: {\"example\":\"...\"}\n"
                "<|im_end|>\n"
                "<|im_start|>assistant\n"
            )
            for temperature in (0.35, 0.55, 0.72):
                try:
                    result = _llm()(
                        prompt,
                        max_tokens=80,
                        temperature=temperature,
                        top_p=0.9,
                        repeat_penalty=1.12,
                        stop=["<|im_end|>", "\n\n"],
                    )
                    raw = result["choices"][0]["text"]
                except Exception:
                    continue
                try:
                    parsed = _extract_json(raw)
                    candidate = _clean_sentence(str(parsed.get("example") or ""), clean_word)
                except Exception:
                    try:
                        candidate = _clean_sentence(raw, clean_word)
                    except Exception:
                        continue
                if _is_bad_example(candidate, clean_word, history):
                    continue
                candidate_quality = _example_quality_score(candidate, clean_word, meaning_cn, book_id)
                if candidate_quality["total"] > best_llm_score["total"]:
                    best_llm_candidate = candidate
                    best_llm_score = candidate_quality
                if candidate_quality["total"] >= QUALITY_PASS_SCORE:
                    example = candidate
                    quality = candidate_quality
                    break
            if example:
                break
            feedback = (
                f"Need better quality. best_total={best_llm_score['total']}. "
                "Fix awkward collocations and avoid meta wording."
            )
        if not example and best_llm_candidate and best_llm_score["total"] >= QUALITY_REGEN_SCORE:
            example = best_llm_candidate
            quality = best_llm_score
    if not example:
        draft, draft_cn, draft_template_id = _template_draft(clean_word, book_id, meaning_cn)
        reviewed = _llm_review_or_rewrite_template(draft, clean_word, book_id, meaning_cn, history) if model_ready else None
        if reviewed:
            example, quality = reviewed
            source = "local_gguf_reviewed_template"
            model_id = MODEL_ID
            template_id = draft_template_id
        elif not model_ready and str(os.environ.get("JACHIN_ENGLISH_EXAMPLE_ALLOW_TEMPLATE_FALLBACK") or "1").strip().lower() not in {
            "0",
            "false",
            "off",
            "no",
        }:
            example = draft
            quality = _example_quality_score(example, clean_word, meaning_cn, book_id)
            source = "local_template_fallback_model_unavailable"
            model_id = FAST_TEMPLATE_ENGINE_ID
            template_id = draft_template_id
            cache[key] = {
                "example": example,
                "example_cn": draft_cn,
                "model_id": model_id,
                "source": source,
                "template_id": template_id,
                "quality": quality,
            }
            _write_cache(cache)
            _remember_example(example, clean_word, template_id=template_id or None)
            return {
                "ok": True,
                "word": clean_word,
                "book_id": book_id,
                "example": example,
                "example_cn": draft_cn,
                "model_id": model_id,
                "source": source,
                "template_id": template_id,
                "runtime_ready": status.get("runtime_ready"),
                "quality": quality,
            }
    if not example:
        return {
            "ok": False,
            "word": clean_word,
            "book_id": book_id,
            "error": "local example model is not ready or did not produce a high-quality sentence",
            "runtime_ready": status.get("runtime_ready"),
            "model_installed": status.get("model_installed"),
            "source": "example_not_ready",
            "model_id": MODEL_ID,
            "quality": quality,
        }

    cache[key] = {
        "example": example,
        "example_cn": "",
        "model_id": model_id,
        "source": source,
        "template_id": template_id,
        "quality": quality,
    }
    _write_cache(cache)
    _remember_example(example, clean_word, template_id=template_id or None)
    return {
        "ok": True,
        "word": clean_word,
        "book_id": book_id,
        "example": example,
        "example_cn": "",
        "model_id": model_id,
        "source": source,
        "template_id": template_id,
        "runtime_ready": status.get("runtime_ready"),
        "quality": quality,
    }
