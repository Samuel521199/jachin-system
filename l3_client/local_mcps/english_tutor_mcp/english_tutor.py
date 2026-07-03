from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CorrectionRule:
    pattern: str
    replacement: str
    note: str


CORRECTION_RULES: list[CorrectionRule] = [
    CorrectionRule(r"\bI very like\b", "I really like", "'very like' is not natural; use 'really like'."),
    CorrectionRule(r"\blike play\b", "like playing", "After 'like', use a gerund or infinitive."),
    CorrectionRule(r"\bgo to home\b", "go home", "Use 'go home', not 'go to home'."),
    CorrectionRule(r"\bdiscuss about\b", "discuss", "'discuss' already means talk about."),
    CorrectionRule(r"\bmore better\b", "better", "Do not use double comparatives."),
    CorrectionRule(r"\badvices\b", "advice", "'Advice' is usually uncountable."),
    CorrectionRule(r"\binformations\b", "information", "'Information' is uncountable."),
    CorrectionRule(r"\bpeoples\b", "people", "'People' is already plural in common use."),
    CorrectionRule(r"\bShe go\b", "She goes", "Third-person singular verbs usually take -s."),
    CorrectionRule(r"\bHe go\b", "He goes", "Third-person singular verbs usually take -s."),
    CorrectionRule(r"\bIt make\b", "It makes", "Third-person singular verbs usually take -s."),
    CorrectionRule(r"\byesterday I go\b", "yesterday I went", "Use past tense for yesterday."),
    CorrectionRule(r"\bI has\b", "I have", "Use 'have' with I/you/we/they."),
    CorrectionRule(r"\bHe have\b", "He has", "Use 'has' with he/she/it."),
    CorrectionRule(r"\bShe have\b", "She has", "Use 'has' with he/she/it."),
    CorrectionRule(r"\ba apple\b", "an apple", "Use 'an' before a vowel sound."),
    CorrectionRule(r"\ban university\b", "a university", "'University' starts with a /ju:/ sound."),
    CorrectionRule(r"\bcan to\b", "can", "Modal verbs are followed by the base verb."),
]


CN_TO_EN: dict[str, str] = {
    "你好": "hello",
    "早上好": "good morning",
    "谢谢": "thank you",
    "会议": "meeting",
    "项目": "project",
    "进展": "progress",
    "功能": "feature",
    "测试": "test",
    "风险": "risk",
    "截止日期": "deadline",
    "我想学习英语": "I want to learn English.",
    "请帮我检查这句话": "Please help me check this sentence.",
    "今天的工作进展很好": "Today's work progress is good.",
}


EN_TO_CN: dict[str, str] = {
    "hello": "你好",
    "good morning": "早上好",
    "thank you": "谢谢",
    "meeting": "会议",
    "project": "项目",
    "progress": "进展",
    "feature": "功能",
    "test": "测试",
    "risk": "风险",
    "deadline": "截止日期",
    "workflow": "工作流",
    "assistant": "助手",
}


WORD_BANK: dict[str, dict[str, Any]] = {
    "abandon": {
        "meaning_cn": "放弃；抛弃",
        "pos": "verb",
        "pronunciation_hint": "uh-BAN-duhn",
        "usage": "Often used when someone gives up a plan, task, or place.",
        "examples": [
            "We should not abandon the test plan too early.",
            "The team abandoned the old workflow after the review.",
        ],
    },
    "improve": {
        "meaning_cn": "改进；提高",
        "pos": "verb",
        "pronunciation_hint": "im-PROOV",
        "usage": "Use it when something becomes better.",
        "examples": [
            "We improved the Lark sending workflow.",
            "Daily practice can improve your spoken English.",
        ],
    },
    "schedule": {
        "meaning_cn": "日程；安排",
        "pos": "noun/verb",
        "pronunciation_hint": "SKEH-jool",
        "usage": "Can mean a timetable or the act of arranging a time.",
        "examples": [
            "Please check tomorrow's schedule.",
            "We scheduled the demo for Friday.",
        ],
    },
    "meeting": {
        "meaning_cn": "会议",
        "pos": "noun",
        "pronunciation_hint": "MEE-ting",
        "usage": "A planned discussion with one or more people.",
        "examples": [
            "The meeting starts at ten.",
            "I sent the meeting notes to Neil.",
        ],
    },
    "project": {
        "meaning_cn": "项目",
        "pos": "noun",
        "pronunciation_hint": "PRAH-jekt",
        "usage": "A planned piece of work with a goal.",
        "examples": [
            "Jachin is an OS assistant project.",
            "The project needs a reliable evidence chain.",
        ],
    },
    "progress": {
        "meaning_cn": "进展",
        "pos": "noun/verb",
        "pronunciation_hint": "PRAH-gress",
        "usage": "Use it to describe movement toward a goal.",
        "examples": [
            "The latest progress is visible in the evidence panel.",
            "We made progress on the install center.",
        ],
    },
    "deadline": {
        "meaning_cn": "截止日期",
        "pos": "noun",
        "pronunciation_hint": "DED-line",
        "usage": "The latest time or date for finishing something.",
        "examples": [
            "The deadline is next Monday.",
            "We need to finish the packaged test before the deadline.",
        ],
    },
    "workflow": {
        "meaning_cn": "工作流",
        "pos": "noun",
        "pronunciation_hint": "WORK-flow",
        "usage": "A sequence of steps for completing a task.",
        "examples": [
            "The Codex to Lark workflow now has evidence.",
            "A stable workflow is better than manual clicking.",
        ],
    },
    "assistant": {
        "meaning_cn": "助手",
        "pos": "noun",
        "pronunciation_hint": "uh-SIS-tuhnt",
        "usage": "Someone or something that helps with tasks.",
        "examples": [
            "Jachin is becoming an OS assistant.",
            "The assistant can open apps and send messages.",
        ],
    },
}


_WORD_DEFINITIONS_CACHE: dict[str, dict[str, str]] | None = None


def _load_word_definitions() -> dict[str, dict[str, str]]:
    global _WORD_DEFINITIONS_CACHE
    if _WORD_DEFINITIONS_CACHE is not None:
        return _WORD_DEFINITIONS_CACHE
    path = Path(__file__).with_name("word_definitions.json")
    if not path.is_file():
        _WORD_DEFINITIONS_CACHE = {}
        return _WORD_DEFINITIONS_CACHE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        _WORD_DEFINITIONS_CACHE = {}
        return _WORD_DEFINITIONS_CACHE
    _WORD_DEFINITIONS_CACHE = {
        str(word).lower(): {
            "meaning_cn": str(value.get("meaning_cn", "")).strip(),
            "pos": str(value.get("part_of_speech", "")).strip() or "-",
        }
        for word, value in raw.items()
        if isinstance(value, dict) and str(value.get("meaning_cn", "")).strip()
    }
    return _WORD_DEFINITIONS_CACHE


QUIZ_ITEMS: list[dict[str, Any]] = [
    {
        "id": "q_project_cn",
        "type": "multiple_choice",
        "question": "What does 'project' mean in Chinese?",
        "choices": ["项目", "会议", "风险", "测试"],
        "answer": "项目",
        "explanation": "'Project' means 项目.",
    },
    {
        "id": "q_go_home",
        "type": "correction",
        "question": "Correct this sentence: I will go to home after work.",
        "answer": "I will go home after work.",
        "explanation": "Use 'go home', not 'go to home'.",
    },
    {
        "id": "q_really_like",
        "type": "correction",
        "question": "Correct this sentence: I very like English.",
        "answer": "I really like English.",
        "explanation": "Use 'really like' instead of 'very like'.",
    },
    {
        "id": "q_deadline",
        "type": "fill_blank",
        "question": "Fill in the blank: The ____ is next Friday.",
        "answer": "deadline",
        "explanation": "'Deadline' means the latest time for finishing something.",
    },
    {
        "id": "q_improve",
        "type": "multiple_choice",
        "question": "Which word means '改进；提高'?",
        "choices": ["improve", "abandon", "meeting", "risk"],
        "answer": "improve",
        "explanation": "'Improve' means 改进 or 提高.",
    },
    {
        "id": "q_workflow",
        "type": "multiple_choice",
        "question": "Which word means '工作流'?",
        "choices": ["workflow", "deadline", "project", "feature"],
        "answer": "workflow",
        "explanation": "'Workflow' means 工作流.",
    },
]


def _normalize_text(text: str | None) -> str:
    return (text or "").strip()


def _capitalize_sentence(text: str) -> str:
    if not text:
        return text
    return text[0].upper() + text[1:]


def _ensure_period(text: str) -> str:
    if not text:
        return text
    if text[-1] in ".!?":
        return text
    return text + "."


def english_correct_sentence(text: str, level: str = "beginner") -> dict[str, Any]:
    original = _normalize_text(text)
    corrected = original
    notes: list[str] = []

    for rule in CORRECTION_RULES:
        updated = re.sub(rule.pattern, rule.replacement, corrected, flags=re.IGNORECASE)
        if updated != corrected:
            notes.append(rule.note)
            corrected = updated

    corrected = re.sub(r"\s+", " ", corrected).strip()
    if corrected:
        corrected = _ensure_period(_capitalize_sentence(corrected))

    changed = corrected != original
    if not notes and original:
        notes.append("No obvious offline-rule issue found. For advanced style feedback, connect an LLM later.")

    return {
        "status": "ok",
        "mode": "offline_rules",
        "intent": "correction",
        "level": level,
        "original": original,
        "corrected": corrected,
        "changed": changed,
        "notes": notes,
    }


def english_translate_cn_en(text: str, direction: str = "auto") -> dict[str, Any]:
    source = _normalize_text(text)
    source_lower = source.lower()
    detected = "cn_to_en" if re.search(r"[\u4e00-\u9fff]", source) else "en_to_cn"
    effective_direction = detected if direction == "auto" else direction

    translation = None
    confidence = "dictionary"
    if effective_direction == "cn_to_en":
        translation = CN_TO_EN.get(source)
        if translation is None:
            parts = [CN_TO_EN.get(part.strip(), part.strip()) for part in re.split(r"[，,。.!?；;]\s*", source) if part.strip()]
            translation = " ".join(parts) if parts else ""
            confidence = "partial_dictionary"
    else:
        translation = EN_TO_CN.get(source_lower)
        if translation is None:
            words = re.findall(r"[A-Za-z]+", source_lower)
            translated_words = [EN_TO_CN.get(word, word) for word in words]
            translation = " ".join(translated_words)
            confidence = "partial_dictionary"

    return {
        "status": "ok",
        "mode": "offline_dictionary",
        "intent": "translation",
        "direction": effective_direction,
        "source": source,
        "translation": translation,
        "confidence": confidence,
        "note": "Offline dictionary supports common words and short phrases only.",
    }


def english_explain_word(word: str) -> dict[str, Any]:
    key = _normalize_text(word).lower()
    key = re.sub(r"[^a-z-]", "", key)
    entry = WORD_BANK.get(key)
    if entry is not None:
        return {
            "status": "ok",
            "mode": "offline_word_bank",
            "intent": "word_explanation",
            "word": key,
            **entry,
        }

    definition = _load_word_definitions().get(key)
    if definition is not None:
        return {
            "status": "ok",
            "mode": "offline_word_definitions",
            "intent": "word_explanation",
            "word": key,
            "meaning_cn": definition["meaning_cn"],
            "pos": definition["pos"],
            "pronunciation_hint": "",
            "usage": f"Use '{key}' according to the sentence context.",
            "examples": [],
        }

    if not key:
        return {
            "status": "not_found",
            "mode": "offline_word_bank",
            "intent": "word_explanation",
            "word": word,
            "message": "Please provide a valid English word.",
            "suggestions": [],
        }

    return {
        "status": "ok",
        "mode": "fallback_contextual_hint",
        "intent": "word_explanation",
        "word": key,
        "meaning_cn": f"{key}：可结合例句语境理解",
        "pos": "-",
        "pronunciation_hint": "",
        "usage": "The word is not in the bundled dictionary; use the sentence context first.",
        "examples": [],
    }


def english_make_examples(topic_or_word: str, count: int = 3, level: str = "beginner") -> dict[str, Any]:
    topic = _normalize_text(topic_or_word)
    safe_count = max(1, min(int(count or 3), 8))
    key = re.sub(r"[^a-z-]", "", topic.lower())

    examples: list[str] = []
    if key in WORD_BANK:
        examples.extend(WORD_BANK[key]["examples"])

    templates = [
        f"The {topic} was easy to notice in the conversation.",
        f"She used {topic} in a short message at work.",
        f"We checked the {topic} before the meeting started.",
        f"He wrote down {topic} in his notebook.",
        f"They talked about {topic} during lunch.",
        f"The teacher gave a simple example with {topic}.",
        f"Please read the sentence with {topic} one more time.",
        f"That {topic} appears often in daily English.",
    ]
    for sentence in templates:
        if sentence not in examples:
            examples.append(sentence)

    return {
        "status": "ok",
        "mode": "offline_templates",
        "intent": "example_sentences",
        "topic_or_word": topic,
        "level": level,
        "examples": examples[:safe_count],
    }


def english_quiz_generate(topic: str = "daily English", count: int = 5, level: str = "beginner") -> dict[str, Any]:
    safe_count = max(1, min(int(count or 5), len(QUIZ_ITEMS)))
    topic_text = _normalize_text(topic) or "daily English"
    topic_lower = topic_text.lower()
    selected = []
    for item in QUIZ_ITEMS:
        blob = " ".join(str(v) for v in item.values()).lower()
        if any(token in blob for token in re.findall(r"[a-z]+", topic_lower)):
            selected.append(item)
    for item in QUIZ_ITEMS:
        if item not in selected:
            selected.append(item)

    public_items = []
    for item in selected[:safe_count]:
        public = {k: v for k, v in item.items() if k != "answer"}
        public_items.append(public)

    return {
        "status": "ok",
        "mode": "offline_quiz_bank",
        "intent": "quiz",
        "topic": topic_text,
        "level": level,
        "questions": public_items,
        "answer_key_available": True,
    }


def english_quiz_check_answer(question_id: str = "", question: str = "", answer: str = "") -> dict[str, Any]:
    qid = _normalize_text(question_id)
    user_answer = _normalize_text(answer)
    item = next((entry for entry in QUIZ_ITEMS if entry["id"] == qid), None)

    if item is None and question:
        q_lower = question.lower()
        item = next((entry for entry in QUIZ_ITEMS if entry["question"].lower() in q_lower or q_lower in entry["question"].lower()), None)

    if item is None:
        return {
            "status": "unknown_question",
            "mode": "offline_quiz_bank",
            "intent": "quiz_check",
            "question_id": qid,
            "answer": user_answer,
            "is_correct": None,
            "message": "No matching offline question was found.",
        }

    expected = str(item["answer"]).strip()
    is_correct = user_answer.lower().strip(".。 ") == expected.lower().strip(".。 ")
    return {
        "status": "ok",
        "mode": "offline_quiz_bank",
        "intent": "quiz_check",
        "question_id": item["id"],
        "answer": user_answer,
        "expected_answer": expected,
        "is_correct": is_correct,
        "explanation": item["explanation"],
    }
