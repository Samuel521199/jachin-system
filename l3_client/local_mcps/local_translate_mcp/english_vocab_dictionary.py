from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class WordEntry:
    word: str
    phonetic: str
    part_of_speech: str
    meaning_cn: str
    example: str
    example_cn: str


_ENTRIES: dict[str, WordEntry] = {
    "agenda": WordEntry("agenda", "/əˈdʒendə/", "n.", "议程；待办事项", "Please share the agenda before the meeting.", "请在会议前分享议程。"),
    "breakfast": WordEntry("breakfast", "/ˈbrekfəst/", "n.", "早餐", "He made breakfast before the early meeting.", "他在早会前做了早餐。"),
    "bread": WordEntry("bread", "/bred/", "n.", "面包", "She bought fresh bread from the bakery.", "她从面包店买了新鲜面包。"),
    "lunch": WordEntry("lunch", "/lʌntʃ/", "n.", "午餐", "They had lunch near the office.", "他们在办公室附近吃了午餐。"),
    "dinner": WordEntry("dinner", "/ˈdɪnər/", "n.", "晚餐；正餐", "The family cooked dinner together tonight.", "这家人今晚一起做了晚餐。"),
    "morning": WordEntry("morning", "/ˈmɔːrnɪŋ/", "n.", "早晨；上午", "She checks her schedule every morning.", "她每天早上查看自己的日程。"),
    "noon": WordEntry("noon", "/nuːn/", "n.", "中午；正午", "The team met at noon for lunch.", "团队中午见面吃午饭。"),
    "message": WordEntry("message", "/ˈmesɪdʒ/", "n./v.", "消息；给某人发消息", "I sent her a message after the meeting.", "会议后我给她发了一条消息。"),
    "budget": WordEntry("budget", "/ˈbʌdʒɪt/", "n./v.", "预算；安排预算", "We adjusted the budget before approving the plan.", "我们在批准计划前调整了预算。"),
    "borrow": WordEntry("borrow", "/ˈbɑːroʊ/", "v.", "借入；借用", "Can I borrow your charger for an hour?", "我可以借你的充电器用一个小时吗？"),
    "deadline": WordEntry("deadline", "/ˈdedlaɪn/", "n.", "截止日期；最后期限", "The deadline moved to Friday afternoon.", "截止日期改到了周五下午。"),
    "feedback": WordEntry("feedback", "/ˈfiːdbæk/", "n.", "反馈；意见", "Her feedback helped us improve the design.", "她的反馈帮助我们改进了设计。"),
    "neighbor": WordEntry("neighbor", "/ˈneɪbər/", "n.", "邻居", "Our neighbor helped carry the heavy boxes upstairs.", "我们的邻居帮忙把重箱子搬上楼。"),
    "upstairs": WordEntry("upstairs", "/ˌʌpˈsterz/", "adv./adj.", "在楼上；往楼上", "Our neighbor helped carry the boxes upstairs.", "我们的邻居帮忙把箱子搬上楼。"),
    "chicken": WordEntry("chicken", "/ˈtʃɪkɪn/", "n.", "鸡肉；鸡", "They cooked chicken soup for dinner.", "他们晚餐做了鸡汤。"),
    "egg": WordEntry("egg", "/eɡ/", "n.", "鸡蛋；蛋", "She boiled an egg for breakfast.", "她早餐煮了一个鸡蛋。"),
    "errand": WordEntry("errand", "/ˈerənd/", "n.", "差事；跑腿任务", "He ran a quick errand after work.", "他下班后办了个小差事。"),
    "weekend": WordEntry("weekend", "/ˈwiːkend/", "n.", "周末", "They visited their parents over the weekend.", "他们周末去看望了父母。"),
    "discuss": WordEntry("discuss", "/dɪˈskʌs/", "v.", "讨论；商量", "We discussed the plan during the meeting.", "我们在会议期间讨论了这个计划。"),
    "send": WordEntry("send", "/send/", "v.", "发送；寄出", "Please send the file before noon.", "请在中午前发送这个文件。"),
    "meeting": WordEntry("meeting", "/ˈmiːtɪŋ/", "n.", "会议；会面", "The meeting starts at ten o'clock.", "会议十点开始。"),
    "office": WordEntry("office", "/ˈɑːfɪs/", "n.", "办公室；办事处", "She left her laptop at the office.", "她把笔记本电脑落在办公室了。"),
    "report": WordEntry("report", "/rɪˈpɔːrt/", "n./v.", "报告；汇报", "He finished the weekly report this morning.", "他今天早上完成了周报。"),
    "project": WordEntry("project", "/ˈprɑːdʒekt/", "n.", "项目；计划", "The project needs a clear timeline.", "这个项目需要清晰的时间表。"),
    "schedule": WordEntry("schedule", "/ˈskedʒuːl/", "n./v.", "日程；安排", "Her schedule is full this afternoon.", "她今天下午的日程很满。"),
    "task": WordEntry("task", "/tæsk/", "n.", "任务；工作", "This task should be finished today.", "这个任务应该今天完成。"),
    "plan": WordEntry("plan", "/plæn/", "n./v.", "计划；打算", "We need a simple plan for tomorrow.", "我们需要一个明天的简单计划。"),
    "file": WordEntry("file", "/faɪl/", "n./v.", "文件；归档", "Save the file before closing the app.", "关闭应用前先保存文件。"),
    "folder": WordEntry("folder", "/ˈfoʊldər/", "n.", "文件夹", "Put the screenshots in this folder.", "把截图放进这个文件夹。"),
    "window": WordEntry("window", "/ˈwɪndoʊ/", "n.", "窗口；窗户", "Open the window on the right side.", "打开右侧的窗口。"),
    "browser": WordEntry("browser", "/ˈbraʊzər/", "n.", "浏览器", "The browser opened the document page.", "浏览器打开了文档页面。"),
    "computer": WordEntry("computer", "/kəmˈpjuːtər/", "n.", "计算机；电脑", "This computer runs the local service.", "这台电脑运行本地服务。"),
    "software": WordEntry("software", "/ˈsɔːftwer/", "n.", "软件", "The software needs a quick update.", "这个软件需要快速更新。"),
    "system": WordEntry("system", "/ˈsɪstəm/", "n.", "系统；体系", "The system saved the user settings.", "系统保存了用户设置。"),
    "service": WordEntry("service", "/ˈsɜːrvɪs/", "n.", "服务；业务", "The local service starts with the app.", "本地服务会随应用启动。"),
    "cache": WordEntry("cache", "/kæʃ/", "n./v.", "缓存；缓存数据", "The cache makes repeated lookups faster.", "缓存让重复查询更快。"),
    "model": WordEntry("model", "/ˈmɑːdəl/", "n.", "模型；范例", "The model translates sentences offline.", "这个模型可以离线翻译句子。"),
    "translate": WordEntry("translate", "/trænzˈleɪt/", "v.", "翻译", "The app can translate the example sentence.", "这个应用可以翻译例句。"),
    "example": WordEntry("example", "/ɪɡˈzæmpəl/", "n.", "例子；例句", "This example shows the word clearly.", "这个例句清楚地展示了这个词。"),
    "meaning": WordEntry("meaning", "/ˈmiːnɪŋ/", "n.", "意思；含义", "Click the word to see its meaning.", "点击单词查看它的意思。"),
    "learn": WordEntry("learn", "/lɜːrn/", "v.", "学习；学会", "Children learn new words every day.", "孩子们每天学习新单词。"),
    "review": WordEntry("review", "/rɪˈvjuː/", "v./n.", "复习；检查；评审", "Review the words again after dinner.", "晚饭后再复习这些单词。"),
    "remember": WordEntry("remember", "/rɪˈmembər/", "v.", "记得；记住", "I remember her name clearly now.", "我现在清楚记得她的名字。"),
    "forget": WordEntry("forget", "/fərˈɡet/", "v.", "忘记", "Do not forget the meeting tomorrow.", "不要忘记明天的会议。"),
    "question": WordEntry("question", "/ˈkwestʃən/", "n./v.", "问题；提问", "She asked a question after class.", "她课后问了一个问题。"),
    "answer": WordEntry("answer", "/ˈænsər/", "n./v.", "答案；回答", "His answer was short and clear.", "他的回答简短清楚。"),
    "change": WordEntry("change", "/tʃeɪndʒ/", "n./v.", "变化；改变", "This change makes the workflow faster.", "这个改动让工作流程更快。"),
    "update": WordEntry("update", "/ˌʌpˈdeɪt/", "n./v.", "更新；最新情况", "Send me an update after lunch.", "午饭后给我发个进展更新。"),
    "issue": WordEntry("issue", "/ˈɪʃuː/", "n.", "问题；事项", "The team fixed the issue quickly.", "团队很快修复了这个问题。"),
    "risk": WordEntry("risk", "/rɪsk/", "n./v.", "风险；冒险", "The manager explained the main risk.", "经理解释了主要风险。"),
    "result": WordEntry("result", "/rɪˈzʌlt/", "n.", "结果；成果", "The result matched our expectation.", "结果符合我们的预期。"),
    "success": WordEntry("success", "/səkˈses/", "n.", "成功；成果", "The demo was a clear success.", "这次演示取得了明确成功。"),
    "failure": WordEntry("failure", "/ˈfeɪljər/", "n.", "失败；故障", "The failure came from a missing file.", "这次故障来自一个缺失文件。"),
    "quick": WordEntry("quick", "/kwɪk/", "adj.", "快速的；迅速的", "She gave a quick reply to the message.", "她很快回复了这条消息。"),
    "slow": WordEntry("slow", "/sloʊ/", "adj.", "慢的；缓慢的", "The slow request made the app feel stuck.", "缓慢的请求让应用看起来卡住了。"),
    "fast": WordEntry("fast", "/fæst/", "adj./adv.", "快的；快速地", "A fast cache improves the user experience.", "快速缓存改善了用户体验。"),
    "local": WordEntry("local", "/ˈloʊkəl/", "adj.", "本地的；当地的", "The local model works without the internet.", "本地模型无需联网也能工作。"),
    "online": WordEntry("online", "/ˌɑːnˈlaɪn/", "adj./adv.", "在线的；联网的", "The online service is only a fallback.", "在线服务只是兜底方案。"),
}


_INFLECTIONS: dict[str, str] = {
    "went": "go",
    "gone": "go",
    "goes": "go",
    "going": "go",
    "bought": "buy",
    "buying": "buy",
    "found": "find",
    "finding": "find",
    "studied": "study",
    "studying": "study",
    "uses": "use",
    "using": "use",
    "used": "use",
    "prepared": "prepare",
    "preparing": "prepare",
    "discussed": "discuss",
    "discussing": "discuss",
    "discusses": "discuss",
    "sent": "send",
    "sending": "send",
    "sends": "send",
    "errands": "errand",
    "meetings": "meeting",
    "messages": "message",
    "tasks": "task",
    "files": "file",
    "folders": "folder",
    "examples": "example",
    "questions": "question",
    "answers": "answer",
    "changes": "change",
    "updated": "update",
    "updating": "update",
    "issues": "issue",
    "risks": "risk",
    "results": "result",
    "models": "model",
    "services": "service",
    "translated": "translate",
    "translating": "translate",
    "learned": "learn",
    "learning": "learn",
    "reviewed": "review",
    "reviewing": "review",
    "remembered": "remember",
    "remembering": "remember",
    "forgot": "forget",
    "forgotten": "forget",
}


_ASSET_NAME = "english_vocab_10k.json"

_EXTRA_ENTRIES: dict[str, WordEntry] = {
    "commute": WordEntry("commute", "/kəˈmjuːt/", "v./n.", "通勤；上下班路程", "Her commute takes forty minutes by subway.", "她坐地铁通勤需要四十分钟。"),
    "grocery": WordEntry("grocery", "/ˈɡroʊsəri/", "n./adj.", "食品杂货；杂货店的", "She wrote milk and bread on the grocery list.", "她把牛奶和面包写在购物清单上。"),
    "receipt": WordEntry("receipt", "/rɪˈsiːt/", "n.", "收据；发票", "She kept the receipt in her wallet.", "她把收据放在钱包里。"),
    "appointment": WordEntry("appointment", "/əˈpɔɪntmənt/", "n.", "预约；约定；任命", "She wrote the appointment in her calendar.", "她把预约写进日历里。"),
    "comfortable": WordEntry("comfortable", "/ˈkʌmftəbl/", "adj.", "舒适的；自在的", "This chair is comfortable for reading.", "这把椅子读书时坐着很舒服。"),
    "weather": WordEntry("weather", "/ˈweðər/", "n.", "天气；气象", "We checked the weather before leaving home.", "我们出门前查看了天气。"),
    "medicine": WordEntry("medicine", "/ˈmedɪsɪn/", "n.", "药；药物；医学", "He takes the medicine after breakfast.", "他早餐后服药。"),
    "exercise": WordEntry("exercise", "/ˈeksərsaɪz/", "n./v.", "运动；练习；锻炼", "Morning exercise gives her more energy.", "晨练让她更有精神。"),
    "oil": WordEntry("oil", "/ɔɪl/", "n./v.", "油；石油；给...加油", "She used a little oil to cook the vegetables.", "她用了一点油来炒蔬菜。"),
    "calm": WordEntry(
        "calm",
        "/kɑːm/",
        "adj./v./n.",
        "平静的；镇静的；使平静",
        "The room became calm after the meeting ended.",
        "会议结束后，房间变得安静下来。",
    ),
    "use": WordEntry(
        "use",
        "/juːz/",
        "v./n.",
        "使用；利用；用途",
        "We use this app to learn new words.",
        "我们使用这个应用学习新单词。",
    ),
    "prepare": WordEntry(
        "prepare",
        "/prɪˈper/",
        "v.",
        "准备；预备；使做好准备",
        "She prepared dinner before the guests arrived.",
        "客人到达前她准备好了晚餐。",
    ),
    "big": WordEntry(
        "big",
        "/bɪɡ/",
        "adj.",
        "大的；重要的；重大的",
        "They made a big decision after the meeting.",
        "会议后他们做了一个重大决定。",
    ),
    "stop": WordEntry(
        "stop",
        "/stɑːp/",
        "v./n.",
        "停止；阻止；车站；停留",
        "The bus stopped near the station.",
        "公交车在车站附近停了下来。",
    ),
    "run": WordEntry(
        "run",
        "/rʌn/",
        "v./n.",
        "跑；运行；经营；一段连续时间",
        "The service can run quietly in the background.",
        "这个服务可以在后台安静运行。",
    ),
    "grab": WordEntry(
        "grab",
        "/græb/",
        "v.",
        "\u6293\u4f4f\uff1b\u62ff\u8d77\uff1b\u8d76\u4e0a\uff1b\u62a2\u5230",
        "I grabbed the bus before it left the station.",
        "\u6211\u5728\u516c\u4ea4\u8f66\u79bb\u7ad9\u524d\u8d76\u4e0a\u4e86\u8f66\u3002",
    ),
    "grabbed": WordEntry(
        "grabbed",
        "/græbd/",
        "v.",
        "grab \u7684\u8fc7\u53bb\u5f0f\uff1b\u6293\u4f4f\uff1b\u62ff\u8d77\uff1b\u8d76\u4e0a",
        "I grabbed the bus before it left the station.",
        "\u6211\u5728\u516c\u4ea4\u8f66\u79bb\u7ad9\u524d\u8d76\u4e0a\u4e86\u8f66\u3002",
    ),
    "grabbing": WordEntry(
        "grabbing",
        "/ˈgræbɪŋ/",
        "v.",
        "grab \u7684\u73b0\u5728\u5206\u8bcd\uff1b\u6293\u4f4f\uff1b\u62ff\u8d77",
        "She is grabbing her bag before the train arrives.",
        "\u706b\u8f66\u5230\u7ad9\u524d\uff0c\u5979\u6b63\u5728\u62ff\u5305\u3002",
    ),
}


@lru_cache(maxsize=1)
def _load_vocab_asset() -> tuple[set[str], dict[str, dict[str, Any]]]:
    path = Path(__file__).resolve().with_name(_ASSET_NAME)
    if not path.exists():
        return set(), {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return set(), {}

    words_raw = raw.get("words") if isinstance(raw, dict) else []
    definitions_raw = raw.get("definitions") if isinstance(raw, dict) else {}
    words = {
        re.sub(r"^[^a-z']+|[^a-z']+$", "", str(item).strip().lower())
        for item in (words_raw if isinstance(words_raw, list) else [])
    }
    words.discard("")

    definitions: dict[str, dict[str, Any]] = {}
    if isinstance(definitions_raw, dict):
        for key, value in definitions_raw.items():
            word = re.sub(r"^[^a-z']+|[^a-z']+$", "", str(key).strip().lower())
            if word and isinstance(value, dict):
                definitions[word] = value
                words.add(word)
    return words, definitions


def _known_words() -> set[str]:
    asset_words, asset_definitions = _load_vocab_asset()
    return set(_ENTRIES.keys()) | set(_EXTRA_ENTRIES.keys()) | set(_INFLECTIONS.keys()) | asset_words | set(asset_definitions.keys())


def normalize_word(raw: str) -> str:
    word = re.sub(r"^[^A-Za-z']+|[^A-Za-z']+$", "", raw or "").lower()
    if word.endswith("'s"):
        word = word[:-2]
    if word in _INFLECTIONS:
        return _INFLECTIONS[word]
    known_words = _known_words()
    if len(word) > 4 and word.endswith("ies"):
        candidate = word[:-3] + "y"
        if candidate in known_words:
            return candidate
    if len(word) > 4 and word.endswith("es") and word[:-2] in known_words:
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and word[:-1] in known_words:
        return word[:-1]
    if len(word) > 5 and word.endswith("ing") and word[:-3] in known_words:
        return word[:-3]
    if len(word) > 4 and word.endswith("ed") and word[:-2] in known_words:
        return word[:-2]
    return word


def _candidate_words(raw: str) -> list[str]:
    word = re.sub(r"^[^A-Za-z']+|[^A-Za-z']+$", "", raw or "").lower()
    if word.endswith("'s"):
        word = word[:-2]
    candidates: list[str] = []

    def add(value: str) -> None:
        value = (value or "").strip().lower()
        if value and value not in candidates:
            candidates.append(value)

    add(word)
    add(_INFLECTIONS.get(word, ""))
    if len(word) > 4 and word.endswith("ies"):
        add(word[:-3] + "y")
    if len(word) > 4 and word.endswith("ied"):
        add(word[:-3] + "y")
    if len(word) > 4 and word.endswith("es"):
        add(word[:-2])
        add(word[:-1])
    if len(word) > 3 and word.endswith("s"):
        add(word[:-1])
    if len(word) > 5 and word.endswith("ing"):
        stem = word[:-3]
        add(stem)
        add(stem + "e")
        if len(stem) > 2 and stem[-1] == stem[-2]:
            add(stem[:-1])
    if len(word) > 4 and word.endswith("ed"):
        stem = word[:-2]
        add(stem)
        add(stem + "e")
        if len(stem) > 2 and stem[-1] == stem[-2]:
            add(stem[:-1])
    if len(word) > 4 and word.endswith("er"):
        stem = word[:-2]
        add(stem)
        if len(stem) > 2 and stem[-1] == stem[-2]:
            add(stem[:-1])
        if word.endswith("ier"):
            add(word[:-3] + "y")
    if len(word) > 5 and word.endswith("est"):
        stem = word[:-3]
        add(stem)
        if len(stem) > 2 and stem[-1] == stem[-2]:
            add(stem[:-1])
        if word.endswith("iest"):
            add(word[:-4] + "y")
    normalized = normalize_word(word)
    add(normalized)
    return candidates


def lookup_word(raw: str) -> dict[str, Any] | None:
    normalized = normalize_word(raw)
    entry = _EXTRA_ENTRIES.get(normalized) or _ENTRIES.get(normalized)
    if not entry:
        return None
    payload = {
        "word": raw.strip().lower() or entry.word,
        "base_word": entry.word,
        "phonetic": entry.phonetic,
        "part_of_speech": entry.part_of_speech,
        "meaning_cn": entry.meaning_cn,
        "example": entry.example,
        "example_cn": entry.example_cn,
        "source": "local_dictionary",
        "model": "local_dictionary_v1",
    }
    if payload["word"] != entry.word:
        payload["meaning_cn"] = f"{payload['word']}：{entry.meaning_cn}"
    return payload


def dictionary_size() -> int:
    return len(_ENTRIES)


def lookup_word(raw: str) -> dict[str, Any] | None:
    asset_words, asset_definitions = _load_vocab_asset()
    candidates = _candidate_words(raw)
    for candidate in candidates:
        entry = _EXTRA_ENTRIES.get(candidate) or _ENTRIES.get(candidate)
        if entry:
            payload = {
                "word": raw.strip().lower() or entry.word,
                "base_word": entry.word,
                "phonetic": entry.phonetic,
                "part_of_speech": entry.part_of_speech,
                "meaning_cn": entry.meaning_cn,
                "example": entry.example,
                "example_cn": entry.example_cn,
                "source": "local_dictionary",
                "model": "local_dictionary_v1",
            }
            if payload["word"] != entry.word:
                payload["meaning_cn"] = f"{payload['word']}: {entry.meaning_cn}"
            return payload

    for candidate in candidates:
        asset_entry = asset_definitions.get(candidate)
        meaning = str((asset_entry or {}).get("meaning_cn") or "").strip()
        if asset_entry and meaning:
            return {
                "word": raw.strip().lower() or candidate,
                "base_word": candidate,
                "phonetic": str(asset_entry.get("phonetic") or "-").strip() or "-",
                "part_of_speech": str(asset_entry.get("part_of_speech") or "-").strip() or "-",
                "meaning_cn": meaning,
                "example": "",
                "example_cn": "",
                "source": "local_dictionary_10k",
                "model": "local_dictionary_10k_v1",
            }

    coverage_word = next((candidate for candidate in candidates if candidate in asset_words), "")
    if coverage_word:
        return {
            "word": raw.strip().lower() or coverage_word,
            "base_word": coverage_word,
            "phonetic": "-",
            "part_of_speech": "-",
            "meaning_cn": "",
            "example": "",
            "example_cn": "",
            "source": "local_dictionary_10k_coverage",
            "model": "google_10000_coverage_v1",
        }
    return None


def dictionary_size() -> int:
    asset_words, asset_definitions = _load_vocab_asset()
    return len(set(_ENTRIES.keys()) | set(_EXTRA_ENTRIES.keys()) | asset_words | set(asset_definitions.keys()))
