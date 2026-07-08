from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORDBOOK_TS = ROOT / "clients" / "desktop" / "src" / "components" / "EnglishVocab" / "wordBookData.ts"
DEFAULT_OUT = ROOT / "l3_client" / "local_mcps" / "local_translate_mcp" / "english_example_pack.json"
LOCAL_MCP = ROOT / "l3_client" / "local_mcps" / "local_translate_mcp"

import sys

sys.path.insert(0, str(LOCAL_MCP))
from english_vocab_dictionary import lookup_word, normalize_word  # noqa: E402


BOOK_ID_BY_EXPORT = {
    "dailyLifeWords": "daily_life_ngsl",
    "workplaceWords": "workplace_business",
    "computerScienceWords": "computer_science",
    "ieltsAcademicWords": "ielts_academic",
    "toeflAcademicWords": "toefl_academic",
}

BOOK_SCENE = {
    "daily_life_ngsl": "daily life",
    "workplace_business": "workplace",
    "computer_science": "software engineering",
    "ielts_academic": "academic writing",
    "toefl_academic": "campus study",
}

DASHSCOPE_DEFAULT_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DASHSCOPE_DEFAULT_MODEL = "qwen-turbo"

TRANSPORT = {"airport", "bus", "car", "flight", "plane", "station", "subway", "taxi", "train"}
PLACE = {"bank", "beach", "clinic", "home", "hospital", "hotel", "kitchen", "market", "office", "restaurant", "room", "school", "store"}
FOOD = {"breakfast", "bread", "chicken", "coffee", "dinner", "egg", "fish", "fruit", "lunch", "milk", "rice", "soup", "tea", "water"}
ACTION = {"borrow", "call", "clean", "cook", "drive", "learn", "listen", "read", "return", "run", "shop", "study", "talk", "travel", "wait", "walk", "work", "write"}
TIME_WORDS = {"morning", "afternoon", "evening", "night", "weekend", "weekday", "today", "tomorrow", "yesterday", "minute", "hour", "day", "week", "month", "year", "date", "time"}
ERRAND_WORDS = {"grocery", "groceries", "receipt", "appointment", "laundry", "schedule", "errand", "delivery", "package", "reservation", "ticket", "bill", "rent"}
COMMUNICATION_WORDS = {"message", "email", "phone", "conversation", "question", "answer", "reply", "feedback", "notice", "announcement", "discussion", "comment", "request"}
PEOPLE_WORDS = {"neighbor", "friend", "family", "parent", "child", "customer", "client", "doctor", "teacher", "student", "colleague", "manager", "guest", "visitor"}
HEALTH_WORDS = {"medicine", "exercise", "health", "pain", "fever", "cough", "sleep", "rest", "energy", "doctor", "clinic", "hospital"}
MONEY_WORDS = {"budget", "price", "cost", "cash", "payment", "salary", "income", "expense", "discount", "tax", "fee", "invoice", "receipt"}
WEATHER_WORDS = {"weather", "rain", "snow", "wind", "sun", "cloud", "temperature", "storm", "sky"}
OBJECT_WORDS = {"bag", "key", "wallet", "phone", "laptop", "computer", "screen", "chair", "table", "door", "window", "book", "notebook", "pen", "photo", "picture"}
MATERIAL_WORDS = {"oil", "gas", "paper", "glass", "wood", "metal", "plastic", "cotton", "stone", "sand", "salt", "sugar"}
ADJECTIVE_WORDS = {"comfortable", "busy", "ready", "tired", "calm", "quiet", "clean", "dirty", "fresh", "heavy", "light", "safe", "careful", "simple", "clear", "early", "late", "quick", "slow"}
TECH_WORDS = {
    "algorithm", "api", "backend", "cache", "compiler", "container", "database", "deployment", "frontend",
    "interface", "latency", "linux", "model", "pipeline", "repository", "server", "service", "system",
    "thread", "token", "runtime", "debug", "bug", "config", "authentication", "authorization", "encryption",
}
ACADEMIC_CONCEPT_WORDS = {
    "analysis", "argument", "concept", "conclusion", "consequence", "evidence", "hypothesis", "impact",
    "infrastructure", "method", "policy", "perspective", "resource", "research", "significant", "sustainable",
    "theory", "trend", "urban",
}
CAMPUS_WORDS = {"lecture", "campus", "professor", "seminar", "assignment", "laboratory", "faculty", "scholarship"}
CURATED_WORDS = {
    "walk",
    "office",
    "hotel",
    "bus",
    "station",
    "airport",
    "grocery",
    "receipt",
    "appointment",
    "comfortable",
    "weather",
    "medicine",
    "exercise",
    "commute",
    "policy",
    "evidence",
    "lecture",
    "oil",
}
TEMPLATE_CATEGORIES = {
    "transport",
    "place",
    "food",
    "action",
    "time",
    "errand",
    "communication",
    "people",
    "health",
    "money",
    "weather",
    "object",
    "material",
    "adjective",
}


def clean_word(raw: str) -> str:
    return re.sub(r"^[^A-Za-z']+|[^A-Za-z']+$", "", raw or "").lower()


def extract_wordbooks(path: Path) -> dict[str, list[str]]:
    text = path.read_text(encoding="utf-8")
    result: dict[str, list[str]] = {}
    for match in re.finditer(r"export const (\w+) = \[", text):
        name = match.group(1)
        if name not in BOOK_ID_BY_EXPORT:
            continue
        end = text.index("] as const;", match.end())
        words: list[str] = []
        seen: set[str] = set()
        for token in re.findall(r"\"([A-Za-z][A-Za-z'-]*)\"", text[match.end() : end]):
            word = clean_word(token)
            if word and word not in seen:
                seen.add(word)
                words.append(word)
        result[BOOK_ID_BY_EXPORT[name]] = words
    return result


def meaning_head(meaning: str, word: str) -> str:
    text = str(meaning or "").strip()
    if not text:
        return word
    if ":" in text:
        text = text.split(":", 1)[1].strip()
    text = re.split(r"[；;，,、/]", text)[0].strip()
    text = re.sub(r"^[a-z]+\.\s*", "", text, flags=re.I)
    return text or word


def guess_pos(word: str, entry: dict[str, Any] | None) -> str:
    pos = str((entry or {}).get("part_of_speech") or "").lower()
    meaning = str((entry or {}).get("meaning_cn") or "")
    if word in ACTION or "v." in pos or "动词" in meaning:
        return "verb"
    if "adj" in pos or "形容" in meaning:
        return "adjective"
    return "noun"


def category(word: str, book_id: str, entry: dict[str, Any] | None) -> str:
    meaning = str((entry or {}).get("meaning_cn") or "")
    if word in TRANSPORT:
        return "transport"
    if word in PLACE:
        return "place"
    if word in FOOD:
        return "food"
    if word in ACTION:
        return "action"
    if word in TIME_WORDS:
        return "time"
    if word in ERRAND_WORDS:
        return "errand"
    if word in COMMUNICATION_WORDS:
        return "communication"
    if word in PEOPLE_WORDS:
        return "people"
    if word in HEALTH_WORDS:
        return "health"
    if word in MONEY_WORDS:
        return "money"
    if word in WEATHER_WORDS:
        return "weather"
    if word in OBJECT_WORDS:
        return "object"
    if word in MATERIAL_WORDS:
        return "material"
    if word in ADJECTIVE_WORDS:
        return "adjective"
    if word in TECH_WORDS:
        return "technical"
    if word in CAMPUS_WORDS:
        return "campus"
    if word in ACADEMIC_CONCEPT_WORDS:
        return "academic"
    if book_id == "computer_science":
        return "technical"
    if book_id == "workplace_business":
        return "workplace"
    if book_id in {"ielts_academic", "toefl_academic"}:
        return "academic"
    if any(key in meaning for key in ["系统", "数据", "模型", "算法", "服务"]):
        return "technical"
    return "general"


def templates(word: str, book_id: str, entry: dict[str, Any] | None) -> list[tuple[str, str, str, str]]:
    meaning = meaning_head(str((entry or {}).get("meaning_cn") or ""), word)
    pos = guess_pos(word, entry)
    cat = category(word, book_id, entry)
    if word == "walk":
        return [
            ("A2", "health", "The doctor told him to walk more every day.", "医生告诉他每天多走路。"),
            ("A2", "commute", "I walk to the office when the weather is good.", "天气好的时候，我走路去办公室。"),
            ("B1", "choice", "She decided to walk home instead of taking a taxi.", "她决定走路回家，而不是打车。"),
            ("A2", "leisure", "After dinner, we walk slowly through the park.", "晚饭后，我们在公园里慢慢散步。"),
            ("B1", "habit", "A short walk helps him clear his mind.", "短暂散步能帮他理清思路。"),
        ]
    if word == "office":
        return [
            ("A2", "workplace", "She left her laptop at the office.", "她把笔记本电脑落在办公室了。"),
            ("A2", "routine", "The office opens at nine every morning.", "办公室每天早上九点开门。"),
            ("B1", "meeting", "We held a quick meeting in the office.", "我们在办公室开了一个简短会议。"),
            ("B1", "workplace", "His office overlooks a quiet street.", "他的办公室可以俯瞰一条安静的街道。"),
            ("B2", "business", "The company moved its office closer to the station.", "公司把办公室搬到了离车站更近的地方。"),
        ]
    if word == "hotel":
        return [
            ("A2", "travel", "We stayed at the hotel during the trip.", "我们旅行时住在这家酒店。"),
            ("A2", "check-in", "The hotel receptionist gave us two room keys.", "酒店前台给了我们两张房卡。"),
            ("B1", "room", "Their hotel room overlooked the river.", "他们的酒店房间可以俯瞰河面。"),
            ("B1", "service", "The hotel staff helped us book a taxi.", "酒店员工帮我们预约了一辆出租车。"),
            ("B2", "travel", "Although the hotel was small, the service felt warm.", "虽然这家酒店不大，但服务让人感觉很温暖。"),
        ]
    if word == "bus":
        return [
            ("A1", "transport", "The bus arrived just as it started to rain.", "刚开始下雨时，公交车正好到了。"),
            ("A2", "commute", "She takes the bus to work every morning.", "她每天早上坐公交车去上班。"),
            ("A2", "travel", "We waited for the bus outside the station.", "我们在车站外等公交车。"),
            ("B1", "delay", "If the bus is late again, I will walk to the office.", "如果公交车又晚点，我就走路去办公室。"),
            ("B1", "city", "The new bus route connects the market and the hospital.", "新的公交线路连接了市场和医院。"),
        ]
    if word == "station":
        return [
            ("A1", "transport", "We met outside the station after work.", "下班后我们在车站外见面。"),
            ("A2", "platform", "The station platform was crowded this morning.", "今天早上车站站台很拥挤。"),
            ("A2", "direction", "She walked through the station to find platform three.", "她穿过车站去找三号站台。"),
            ("B1", "delay", "A screen inside the station showed the train delay.", "车站内的屏幕显示火车延误。"),
            ("B1", "city", "The new station entrance is closer to the market.", "新的车站入口离市场更近。"),
        ]
    if word == "airport":
        return [
            ("A2", "travel", "We arrived at the airport before sunrise.", "我们在日出前到达了机场。"),
            ("A2", "security", "The airport security line moved faster than expected.", "机场安检队伍比预想中前进得更快。"),
            ("B1", "flight", "She checked the airport screen for her flight number.", "她查看机场屏幕上的航班号。"),
            ("B1", "travel", "The airport was crowded during the holiday weekend.", "假期周末机场非常拥挤。"),
            ("B2", "delay", "Bad weather forced the airport to delay several flights.", "恶劣天气迫使机场延误了几趟航班。"),
        ]
    if word == "grocery":
        return [
            ("A1", "shopping", "She wrote milk and bread on the grocery list.", "她把牛奶和面包写在购物清单上。"),
            ("A2", "store", "He stopped at the grocery store after work.", "他下班后去了杂货店。"),
            ("A2", "family", "We do our grocery shopping on Saturday morning.", "我们周六早上采购日用品。"),
            ("B1", "budget", "The grocery bill was lower than expected.", "这次杂货账单比预期低。"),
            ("B1", "planning", "Online grocery delivery saved them an extra trip.", "线上杂货配送让他们少跑了一趟。"),
        ]
    if word == "receipt":
        return [
            ("A1", "shopping", "She kept the receipt in her wallet.", "她把收据放在钱包里。"),
            ("A2", "return", "You need the receipt to return the shirt.", "退这件衬衫需要收据。"),
            ("A2", "expense", "He took a photo of the receipt after shopping.", "购物后他拍下了收据。"),
            ("B1", "work", "The accountant checked every receipt carefully.", "会计仔细核对了每张收据。"),
            ("B1", "budget", "Keeping the receipt helped them track spending.", "保留收据帮助他们追踪开支。"),
        ]
    if word == "appointment":
        return [
            ("A1", "calendar", "She wrote the appointment in her calendar.", "她把预约写进日历里。"),
            ("A2", "clinic", "His dentist appointment is tomorrow morning.", "他的牙医预约在明天早上。"),
            ("A2", "reminder", "The phone reminded her of the appointment.", "手机提醒了她这个预约。"),
            ("B1", "schedule", "They moved the appointment to Friday afternoon.", "他们把预约改到了周五下午。"),
            ("B1", "planning", "Please confirm the appointment before you leave.", "离开前请确认预约。"),
        ]
    if word == "comfortable":
        return [
            ("A1", "home", "This chair is comfortable for reading.", "这把椅子读书时坐着很舒服。"),
            ("A2", "clothes", "She wore comfortable shoes for the long walk.", "她穿了舒服的鞋去长时间步行。"),
            ("A2", "room", "The room felt comfortable after we opened the window.", "打开窗户后，房间感觉很舒服。"),
            ("B1", "travel", "The hotel bed was clean and comfortable.", "酒店的床干净又舒服。"),
            ("B1", "work", "A comfortable workspace helps people focus.", "舒适的工作空间有助于人们集中注意力。"),
        ]
    if word == "weather":
        return [
            ("A1", "daily", "The weather was nice enough for a walk.", "天气很好，适合散步。"),
            ("A2", "travel", "We checked the weather before leaving home.", "我们出门前查看了天气。"),
            ("B1", "plan", "Bad weather changed our weekend plan.", "糟糕的天气改变了我们的周末计划。"),
            ("B1", "mood", "Warm weather brought people outside.", "温暖的天气让人们走到户外。"),
            ("B2", "delay", "Stormy weather delayed several afternoon trains.", "暴风雨天气导致几趟下午的火车延误。"),
        ]
    if word == "medicine":
        return [
            ("A1", "routine", "He takes the medicine after breakfast.", "他早餐后服药。"),
            ("A2", "doctor", "The doctor gave her new medicine for the cough.", "医生给她开了新的止咳药。"),
            ("A2", "safety", "Keep the medicine away from children.", "请把药放在儿童接触不到的地方。"),
            ("B1", "recovery", "The medicine helped him feel better by evening.", "到晚上时，这种药让他感觉好些了。"),
            ("B1", "instruction", "Read the label before taking the medicine.", "服药前请阅读标签。"),
        ]
    if word == "exercise":
        return [
            ("A1", "health", "Morning exercise gives her more energy.", "晨练让她更有精神。"),
            ("A2", "routine", "He does light exercise after work.", "他下班后做轻量运动。"),
            ("A2", "school", "The teacher gave us a grammar exercise.", "老师给了我们一道语法练习。"),
            ("B1", "habit", "Regular exercise helped him sleep better.", "规律运动帮助他睡得更好。"),
            ("B1", "balance", "Exercise and rest are both important.", "运动和休息都很重要。"),
        ]
    if word == "commute":
        return [
            ("A2", "transport", "Her commute takes forty minutes by subway.", "她坐地铁通勤需要四十分钟。"),
            ("A2", "routine", "He listens to podcasts during his commute.", "他通勤时听播客。"),
            ("B1", "delay", "Heavy rain made the commute much slower.", "大雨让通勤慢了很多。"),
            ("B1", "work", "A shorter commute gives her more family time.", "更短的通勤让她有更多时间陪家人。"),
            ("B2", "planning", "Remote work reduced their weekly commute.", "远程办公减少了他们每周的通勤。"),
        ]
    if word == "policy":
        return [
            ("B1", "workplace", "The new policy explains how overtime is approved.", "新政策说明了加班如何审批。"),
            ("B1", "public", "The policy aims to reduce waste in public buildings.", "这项政策旨在减少公共建筑中的浪费。"),
            ("B2", "education", "Schools changed their policy after parents raised concerns.", "家长提出担忧后，学校修改了政策。"),
            ("B2", "analysis", "The report compares the policy with earlier reforms.", "报告把这项政策和早期改革进行了比较。"),
            ("C1", "debate", "Critics argued that the policy ignored rural communities.", "批评者认为这项政策忽视了农村社区。"),
        ]
    if word == "evidence":
        return [
            ("B1", "argument", "The lawyer presented new evidence in court.", "律师在法庭上提交了新证据。"),
            ("B1", "research", "The study found clear evidence of climate change.", "这项研究发现了气候变化的明确证据。"),
            ("B2", "essay", "Use evidence to support each main point in the essay.", "在文章中用证据支持每个主要观点。"),
            ("B2", "discussion", "Without evidence, the claim sounded weak.", "没有证据，这个说法听起来很薄弱。"),
            ("C1", "analysis", "The report questions whether the evidence is reliable.", "报告质疑这些证据是否可靠。"),
        ]
    if word == "lecture":
        return [
            ("A2", "campus", "The lecture starts at ten in the main hall.", "讲座十点在主厅开始。"),
            ("B1", "notes", "She took careful notes during the lecture.", "她在讲座中认真记笔记。"),
            ("B1", "professor", "The professor ended the lecture with a question.", "教授用一个问题结束了讲座。"),
            ("B2", "topic", "Today's lecture focused on renewable energy.", "今天的讲座聚焦可再生能源。"),
            ("B2", "review", "Students discussed the lecture after class.", "学生们课后讨论了这场讲座。"),
        ]
    if cat == "transport":
        return [
            ("A2", "schedule", f"The {word} arrived earlier than the timetable showed.", f"{meaning}比时刻表上显示的时间更早到了。"),
            ("A2", "travel", f"She checked the {word} schedule before leaving home.", f"她出门前查了{meaning}的时刻表。"),
            ("B1", "delay", f"The {word} was delayed because of heavy rain.", f"由于大雨，{meaning}延误了。"),
            ("B1", "city", f"This {word} route is useful for daily commuting.", f"这条{meaning}线路对日常通勤很有用。"),
            ("B2", "planning", f"We compared two {word} options before booking the trip.", f"预订行程前，我们比较了两种{meaning}方案。"),
        ]
    if cat == "place":
        return [
            ("A2", "location", f"The {word} is only five minutes from here.", f"{meaning}离这里只有五分钟路程。"),
            ("A2", "visit", f"We visited the {word} after breakfast.", f"早饭后我们去了{meaning}。"),
            ("B1", "service", f"The staff at the {word} answered our questions patiently.", f"{meaning}的工作人员耐心回答了我们的问题。"),
            ("B1", "description", f"The {word} was quiet when we arrived.", f"我们到达时，{meaning}很安静。"),
            ("B2", "choice", f"They chose this {word} because it was convenient and clean.", f"他们选择这个{meaning}，因为它方便又干净。"),
        ]
    if cat == "food":
        return [
            ("A1", "meal", f"She had {word} with her family.", f"她和家人一起吃了{meaning}。"),
            ("A2", "shopping", f"He bought fresh {word} from the market.", f"他从市场买了新鲜的{meaning}。"),
            ("A2", "kitchen", f"We prepared {word} in the kitchen.", f"我们在厨房准备了{meaning}。"),
            ("B1", "preference", f"The children asked for {word} after school.", f"孩子们放学后想要{meaning}。"),
            ("B1", "health", f"Too much {word} can make the meal feel heavy.", f"太多{meaning}会让这顿饭显得很腻。"),
        ]
    if cat == "time":
        return [
            ("A1", "routine", f"I usually check my messages in the {word}.", f"我通常在{meaning}查看消息。"),
            ("A2", "planning", f"She saved that {word} for quiet reading.", f"她把那个{meaning}留给安静阅读。"),
            ("A2", "family", f"We called our parents that {word}.", f"那个{meaning}我们给父母打了电话。"),
            ("B1", "memory", f"The {word} felt calm after the rain stopped.", f"雨停之后，那个{meaning}显得很安静。"),
            ("B1", "schedule", f"He keeps the same routine every {word}.", f"他每个{meaning}都保持同样的日程。"),
        ]
    if cat == "errand":
        return [
            ("A2", "home", f"She added the {word} to her weekend list.", f"她把{meaning}加到了周末清单里。"),
            ("A2", "planning", f"He checked the {word} before leaving home.", f"他出门前检查了{meaning}。"),
            ("B1", "organization", f"The {word} helped them keep the day organized.", f"{meaning}帮助他们把一天安排得更有条理。"),
            ("B1", "family", f"They talked about the {word} over breakfast.", f"他们早餐时聊到了{meaning}。"),
            ("B2", "decision", f"A small change in the {word} saved them time.", f"{meaning}上的一个小调整帮他们节省了时间。"),
        ]
    if cat == "communication":
        return [
            ("A2", "phone", f"She sent a short {word} before the meeting.", f"她在会议前发了一条简短的{meaning}。"),
            ("A2", "work", f"His {word} made the plan easier to understand.", f"他的{meaning}让计划更容易理解。"),
            ("B1", "reply", f"We waited for her {word} before making a decision.", f"我们等她的{meaning}后再做决定。"),
            ("B1", "team", f"The team shared the {word} in the group chat.", f"团队在群聊里分享了这条{meaning}。"),
            ("B2", "clarity", f"A clear {word} can prevent many mistakes.", f"清晰的{meaning}可以避免很多错误。"),
        ]
    if cat == "people":
        return [
            ("A1", "daily", f"My {word} waved when I opened the door.", f"我开门时，我的{meaning}挥了挥手。"),
            ("A2", "help", f"The {word} helped us carry the boxes upstairs.", f"{meaning}帮我们把箱子搬上楼。"),
            ("B1", "conversation", f"She had a friendly talk with her {word}.", f"她和她的{meaning}友好地聊了一会儿。"),
            ("B1", "community", f"Everyone thanked the {word} for the quick help.", f"大家都感谢这位{meaning}及时帮忙。"),
            ("B2", "trust", f"A good {word} can make a busy day easier.", f"一位好的{meaning}能让忙碌的一天轻松一些。"),
        ]
    if cat == "health":
        return [
            ("A2", "routine", f"He takes the {word} after breakfast.", f"他早餐后服用{meaning}。"),
            ("A2", "advice", f"The doctor gave simple advice about {word}.", f"医生给出了关于{meaning}的简单建议。"),
            ("B1", "habit", f"Regular {word} helped her sleep better.", f"规律的{meaning}帮助她睡得更好。"),
            ("B1", "care", f"They talked about {word} during the checkup.", f"他们在体检时谈到了{meaning}。"),
            ("B2", "balance", f"Good {word} depends on sleep, food, and movement.", f"良好的{meaning}取决于睡眠、饮食和运动。"),
        ]
    if cat == "money":
        return [
            ("A2", "shopping", f"She checked the {word} before buying the coat.", f"她买外套前看了{meaning}。"),
            ("A2", "home", f"The family discussed the {word} at dinner.", f"这家人晚饭时讨论了{meaning}。"),
            ("B1", "planning", f"A clear {word} helped them avoid extra costs.", f"清晰的{meaning}帮助他们避免了额外开支。"),
            ("B1", "work", f"The manager asked for the updated {word}.", f"经理要了更新后的{meaning}。"),
            ("B2", "decision", f"The final {word} changed their travel plan.", f"最终的{meaning}改变了他们的旅行计划。"),
        ]
    if cat == "weather":
        return [
            ("A1", "daily", f"The {word} was nice enough for a walk.", f"{meaning}很好，适合散步。"),
            ("A2", "travel", f"We checked the {word} before leaving home.", f"我们出门前查看了{meaning}。"),
            ("B1", "plan", f"The bad {word} changed our weekend plan.", f"糟糕的{meaning}改变了我们的周末计划。"),
            ("B1", "mood", f"The bright {word} made the room feel warmer.", f"明亮的{meaning}让房间感觉更温暖。"),
            ("B2", "delay", f"The sudden {word} delayed several afternoon trains.", f"突如其来的{meaning}导致几趟下午的火车延误。"),
        ]
    if cat == "object":
        return [
            ("A1", "home", f"She put the {word} on the table.", f"她把{meaning}放在桌上。"),
            ("A2", "lost", f"He found his {word} under the chair.", f"他在椅子下面找到了自己的{meaning}。"),
            ("B1", "work", f"The {word} was useful during the meeting.", f"{meaning}在会议中很有用。"),
            ("B1", "travel", f"Please keep the {word} in your bag.", f"请把{meaning}放在包里。"),
            ("B2", "detail", f"The old {word} reminded her of college life.", f"那个旧{meaning}让她想起了大学生活。"),
        ]
    if cat == "material":
        return [
            ("A1", "kitchen", f"She used a little {word} to cook the vegetables.", f"她用了一点{meaning}来炒蔬菜。"),
            ("A2", "home", f"He cleaned the {word} from the kitchen counter.", f"他把厨房台面上的{meaning}擦干净了。"),
            ("B1", "shopping", f"We bought {word} before preparing dinner.", f"我们准备晚饭前买了{meaning}。"),
            ("B1", "safety", f"Please keep the {word} away from the fire.", f"请让{meaning}远离火源。"),
            ("B2", "environment", f"The factory reduced its use of {word} this year.", f"这家工厂今年减少了{meaning}的使用。"),
        ]
    if cat == "adjective":
        return [
            ("A1", "feeling", f"The room felt {word} after we opened the window.", f"打开窗户后，房间感觉很{meaning}。"),
            ("A2", "daily", f"She chose a {word} chair for reading.", f"她选了一把{meaning}的椅子来读书。"),
            ("B1", "work", f"A {word} plan helped everyone move faster.", f"一个{meaning}的计划让大家推进得更快。"),
            ("B1", "travel", f"The hotel was small but {word}.", f"这家酒店不大，但很{meaning}。"),
            ("B2", "judgment", f"His {word} answer made the discussion easier.", f"他{meaning}的回答让讨论更顺畅。"),
        ]
    if pos == "verb":
        return [
            ("A2", "routine", f"I need to {word} before the day gets busy.", f"我需要在一天变忙之前先{meaning}。"),
            ("A2", "habit", f"She likes to {word} when the weather is calm.", f"天气平静的时候，她喜欢{meaning}。"),
            ("B1", "teamwork", f"We decided to {word} after a short discussion.", f"简短讨论后，我们决定{meaning}。"),
            ("B1", "progress", f"He stopped for a moment, then continued to {word}.", f"他停了一会儿，然后继续{meaning}。"),
            ("B2", "planning", f"The team learned when to {word} and when to wait.", f"团队学会了什么时候该{meaning}，什么时候该等待。"),
        ]
    if cat == "technical":
        return [
            ("A2", "software", f"The engineer checked the {word} before deployment.", f"工程师在部署前检查了{meaning}。"),
            ("B1", "debugging", f"Our logs showed a problem with the {word}.", f"日志显示{meaning}存在问题。"),
            ("B1", "system", f"The new {word} improved the service response time.", f"新的{meaning}提升了服务响应速度。"),
            ("B2", "architecture", f"We documented how the {word} works inside the system.", f"我们记录了{meaning}在系统内部如何工作。"),
            ("B2", "review", f"The team reviewed the {word} before merging the change.", f"团队在合并变更前审查了{meaning}。"),
        ]
    if cat == "workplace":
        return [
            ("A2", "meeting", f"The team discussed the {word} during the meeting.", f"团队在会议中讨论了{meaning}。"),
            ("A2", "report", f"Please include the {word} in today's report.", f"请把{meaning}写进今天的报告里。"),
            ("B1", "planning", f"The manager asked for a clearer {word}.", f"经理要求更清晰的{meaning}。"),
            ("B1", "delivery", f"This {word} affects our delivery plan.", f"这个{meaning}会影响我们的交付计划。"),
            ("B2", "strategy", f"Leadership reviewed the {word} before making a decision.", f"管理层在做决定前审查了{meaning}。"),
        ]
    if cat == "academic":
        return [
            ("B1", "study", f"The lecture introduced the concept of {word}.", f"讲座介绍了{meaning}这个概念。"),
            ("B1", "essay", f"A good essay should define {word} clearly.", f"一篇好文章应该清楚定义{meaning}。"),
            ("B2", "evidence", f"The researcher used data to explain {word}.", f"研究者用数据解释了{meaning}。"),
            ("B2", "analysis", f"Students compared different views on {word}.", f"学生们比较了关于{meaning}的不同观点。"),
            ("C1", "argument", f"The article questions whether {word} can solve the problem.", f"文章质疑{meaning}是否能解决这个问题。"),
        ]
    return [
        ("A2", "reading", f"I saw {word} in a short news article.", f"我在一篇短新闻里看到了{meaning}。"),
        ("A2", "classroom", f"The teacher wrote {word} on the board.", f"老师把{meaning}写在黑板上。"),
        ("B1", "conversation", f"Someone mentioned {word} during the discussion.", f"有人在讨论中提到了{meaning}。"),
        ("B1", "example", f"The sentence used {word} in a clear context.", f"这个句子在清晰语境中使用了{meaning}。"),
        ("B2", "learning", f"Seeing {word} in context made it easier to remember.", f"在语境中看到{meaning}让它更容易记住。"),
    ]


BAD_FRAGMENTS = [
    "want to learn the word",
    "want to remember",
    "came up in a normal conversation",
    "on my way home",
    "after lunch",
    "discount on office",
    "hotel on the kitchen table",
    "explained the morning",
    "question about the morning",
    "meaning of morning",
    "in a short news article",
    "wrote policy on the board",
    "the sentence used",
    "in a clear context",
    "seeing policy in context",
    "seeing ",
    " in context made it easier",
]


SUBJECTS = [
    ("I", "我"),
    ("She", "她"),
    ("He", "他"),
    ("We", "我们"),
    ("They", "他们"),
    ("The team", "团队"),
    ("The manager", "经理"),
    ("A student", "一名学生"),
]

SCENE_DETAIL = {
    "daily_life_ngsl": [
        ("before leaving home", "出门前"),
        ("on a rainy morning", "一个下雨的早晨"),
        ("during a busy weekend", "一个忙碌的周末"),
        ("while waiting in line", "排队等候时"),
        ("after checking the calendar", "查看日历后"),
        ("near the kitchen window", "在厨房窗边"),
        ("at the end of the day", "一天结束时"),
    ],
    "workplace_business": [
        ("before the client call", "客户电话前"),
        ("during the weekly review", "周度复盘时"),
        ("after the project update", "项目更新后"),
        ("in the planning meeting", "规划会议中"),
        ("before sending the report", "发送报告前"),
        ("while checking the dashboard", "查看看板时"),
    ],
    "computer_science": [
        ("before deployment", "部署前"),
        ("while reading the logs", "查看日志时"),
        ("during the code review", "代码评审时"),
        ("after the service restarted", "服务重启后"),
        ("inside the test environment", "在测试环境中"),
        ("when latency increased", "延迟上升时"),
    ],
    "ielts_academic": [
        ("in the introduction", "在引言中"),
        ("when presenting evidence", "呈现证据时"),
        ("in a public policy debate", "在公共政策讨论中"),
        ("while comparing two studies", "比较两项研究时"),
        ("in the final paragraph", "在最后一段中"),
    ],
    "toefl_academic": [
        ("during the lecture", "讲座中"),
        ("in the campus library", "在校园图书馆里"),
        ("while preparing an assignment", "准备作业时"),
        ("after the professor's question", "教授提问后"),
        ("during the lab discussion", "实验室讨论中"),
    ],
}

CATEGORY_DETAILS = {
    "time": [("when the street was still quiet", "街上还很安静时"), ("before the first meeting", "第一次会议前"), ("as the light changed", "光线变化时")],
    "errand": [("beside the front door", "在前门旁"), ("on the kitchen counter", "在厨房台面上"), ("inside the weekend checklist", "在周末清单里")],
    "communication": [("in the group chat", "在群聊里"), ("before anyone made a decision", "大家做决定前"), ("with a clear subject line", "带着清楚的标题")],
    "people": [("outside the elevator", "电梯外"), ("near the apartment gate", "公寓门口附近"), ("during a small favor", "帮一个小忙时")],
    "health": [("after the checkup", "体检后"), ("before going to bed", "睡前"), ("during a quiet recovery week", "安静恢复的一周里")],
    "money": [("before paying the bill", "付款前"), ("inside the monthly plan", "在月度计划里"), ("while comparing two options", "比较两个选项时")],
    "weather": [("above the river", "在河面上方"), ("outside the station", "车站外"), ("just before sunset", "日落前")],
    "object": [("inside her backpack", "在她的背包里"), ("beside the window", "窗边"), ("on the meeting table", "会议桌上")],
    "material": [("near the stove", "炉灶旁"), ("in the workshop", "在车间里"), ("on the shelf", "架子上")],
}

FRAME_BANK = {
    "daily_life_ngsl": [
        ("A2", "small_moment", "{subject} noticed the {word} {detail}.", "{subject_cn}{detail_cn}注意到了{meaning}。"),
        ("A2", "routine", "{subject} kept the {word} ready {detail}.", "{subject_cn}{detail_cn}把{meaning}准备好。"),
        ("B1", "choice", "{subject} changed the plan because of the {word}.", "{subject_cn}因为{meaning}改变了计划。"),
        ("B1", "memory", "That {word} reminded {object_pronoun} of a quieter day.", "那个{meaning}让{object_pronoun_cn}想起了更安静的一天。"),
        ("B2", "contrast", "The {word} seemed ordinary, but it solved the problem.", "{meaning}看起来普通，却解决了问题。"),
        ("A2", "conversation", "{subject} mentioned the {word} {detail}.", "{subject_cn}{detail_cn}提到了{meaning}。"),
        ("B1", "practical", "A better {word} would save everyone some time.", "更好的{meaning}会帮大家省下一些时间。"),
        ("A2", "surprise", "{subject} found the {word} when the drawer was opened.", "{subject_cn}打开抽屉时找到了{meaning}。"),
        ("B1", "repair", "The missing {word} explained why the plan felt unfinished.", "缺少的{meaning}解释了为什么计划显得不完整。"),
        ("A2", "neighbor", "A neighbor asked about the {word} near the front gate.", "一位邻居在前门附近问起了{meaning}。"),
        ("B1", "detail", "One small {word} made the whole morning easier.", "一个小小的{meaning}让整个早晨轻松了许多。"),
        ("B2", "turning_point", "Without the {word}, the day would have gone differently.", "如果没有{meaning}，这一天会完全不同。"),
    ],
    "workplace_business": [
        ("A2", "meeting", "{subject} brought up the {word} {detail}.", "{subject_cn}{detail_cn}提出了{meaning}。"),
        ("B1", "decision", "The {word} changed how the team set priorities.", "{meaning}改变了团队设定优先级的方式。"),
        ("B1", "handover", "{subject} added the {word} to the handover notes.", "{subject_cn}把{meaning}加进了交接备注。"),
        ("B2", "risk", "A missing {word} created risk for the delivery plan.", "缺少{meaning}给交付计划带来了风险。"),
        ("B2", "alignment", "The team clarified the {word} before moving forward.", "团队在继续推进前澄清了{meaning}。"),
        ("A2", "report", "{subject} summarized the {word} in one paragraph.", "{subject_cn}用一段话总结了{meaning}。"),
        ("B1", "follow_up", "The {word} became the first follow-up item.", "{meaning}成了第一项跟进事项。"),
        ("B1", "client", "The client asked why the {word} changed this week.", "客户询问{meaning}为什么本周发生变化。"),
        ("B2", "tradeoff", "A stronger {word} would slow the launch but reduce risk.", "更强的{meaning}会放慢上线，但能降低风险。"),
        ("A2", "note", "{subject} marked the {word} in the shared document.", "{subject_cn}在共享文档中标记了{meaning}。"),
        ("B1", "timeline", "The timeline depends on the {word} being ready first.", "时间线取决于{meaning}先准备好。"),
        ("B2", "escalation", "Leadership escalated the {word} after the second delay.", "第二次延误后，管理层升级处理了{meaning}。"),
    ],
    "computer_science": [
        ("A2", "debugging", "{subject} checked the {word} {detail}.", "{subject_cn}{detail_cn}检查了{meaning}。"),
        ("B1", "incident", "The {word} helped explain the sudden error.", "{meaning}帮助解释了这次突然错误。"),
        ("B1", "review", "{subject} documented the {word} during the code review.", "{subject_cn}在代码评审时记录了{meaning}。"),
        ("B2", "architecture", "A cleaner {word} made the system easier to maintain.", "更清晰的{meaning}让系统更容易维护。"),
        ("B2", "performance", "The new {word} reduced work inside the service.", "新的{meaning}减少了服务内部的工作量。"),
        ("A2", "testing", "The test failed when the {word} changed.", "{meaning}变化后，测试失败了。"),
        ("B1", "deployment", "{subject} verified the {word} before deployment.", "{subject_cn}在部署前验证了{meaning}。"),
        ("B1", "trace", "A trace file showed where the {word} slowed down.", "追踪文件显示了{meaning}变慢的位置。"),
        ("B2", "rollback", "The rollback was safer after the {word} was isolated.", "{meaning}被隔离后，回滚更安全了。"),
        ("A2", "config", "{subject} updated the {word} in the staging config.", "{subject_cn}在预发配置中更新了{meaning}。"),
        ("B1", "monitoring", "Monitoring caught the {word} before users noticed.", "监控在用户察觉前捕捉到了{meaning}。"),
        ("B2", "boundary", "The design keeps the {word} outside the core process.", "这个设计把{meaning}放在核心进程之外。"),
    ],
    "ielts_academic": [
        ("B1", "essay", "The essay links {word} to a wider social issue.", "这篇文章把{meaning}和更广泛的社会问题联系起来。"),
        ("B2", "evidence", "The study uses {word} to support its argument.", "这项研究用{meaning}支持论点。"),
        ("B2", "policy", "Public debate around {word} has become more complex.", "围绕{meaning}的公共讨论变得更复杂。"),
        ("C1", "contrast", "Different groups interpret {word} in very different ways.", "不同群体对{meaning}的理解差异很大。"),
        ("B1", "definition", "The paragraph defines {word} before giving examples.", "这一段先定义{meaning}，再给出例子。"),
        ("B2", "trend", "Recent data shows a clear change in {word}.", "近期数据显示{meaning}发生了明显变化。"),
        ("B1", "cause", "The author treats {word} as one possible cause.", "作者把{meaning}视为一种可能原因。"),
        ("B2", "limitation", "The study notes that {word} varies across regions.", "研究指出{meaning}在不同地区有所差异。"),
        ("C1", "nuance", "The conclusion warns against oversimplifying {word}.", "结论提醒人们不要过度简化{meaning}。"),
        ("B2", "comparison", "The chart compares {word} across three age groups.", "图表比较了三个年龄组中的{meaning}。"),
        ("C1", "implication", "If {word} continues to rise, cities may need new rules.", "如果{meaning}继续上升，城市可能需要新规则。"),
    ],
    "toefl_academic": [
        ("B1", "lecture", "The professor explained {word} with a classroom example.", "教授用课堂例子解释了{meaning}。"),
        ("B1", "assignment", "{subject} used {word} in the research assignment.", "{subject_cn}在研究作业中使用了{meaning}。"),
        ("B2", "seminar", "The seminar compared two theories about {word}.", "研讨课比较了关于{meaning}的两种理论。"),
        ("B1", "campus", "The student asked how {word} appears in real studies.", "学生问{meaning}如何出现在真实研究中。"),
        ("B2", "lab", "The lab report described the role of {word}.", "实验报告描述了{meaning}的作用。"),
        ("B1", "office_hours", "The professor discussed {word} during office hours.", "教授在答疑时间讨论了{meaning}。"),
        ("B2", "fieldwork", "The field notes included a clear example of {word}.", "田野笔记里包含了一个清楚的{meaning}例子。"),
        ("B1", "quiz", "The quiz asked students to recognize {word}.", "测验要求学生识别{meaning}。"),
        ("B2", "research", "A recent paper challenged the usual view of {word}.", "一篇近期论文挑战了对{meaning}的常见看法。"),
    ],
}


def _pick(options: list[tuple[str, str]] | list[tuple[str, str, str, str]], seed: str, offset: int = 0) -> Any:
    if not options:
        return None
    return options[(sum(ord(ch) for ch in seed) + offset * 17) % len(options)]


def _subject(seed: str, idx: int) -> dict[str, str]:
    subject, subject_cn = _pick(SUBJECTS, seed, idx)
    object_pronoun = "her" if subject == "She" else "him" if subject == "He" else "them" if subject in {"They", "The team"} else "me"
    object_pronoun_cn = "她" if subject == "She" else "他" if subject == "He" else "他们" if subject in {"They", "The team"} else "我"
    return {
        "subject": subject,
        "subject_cn": subject_cn,
        "object_pronoun": object_pronoun,
        "object_pronoun_cn": object_pronoun_cn,
    }


def _details(book_id: str, cat: str, seed: str, idx: int) -> tuple[str, str]:
    options = CATEGORY_DETAILS.get(cat) or SCENE_DETAIL.get(book_id) or SCENE_DETAIL["daily_life_ngsl"]
    return _pick(options, seed, idx)


def _format_frame(pattern: str, values: dict[str, str]) -> str:
    text = pattern.format(**values)
    return re.sub(r"\s+", " ", text).strip()


def semantic_blueprints(word: str, book_id: str, entry: dict[str, Any] | None) -> list[tuple[str, str, str, str]]:
    meaning = meaning_head(str((entry or {}).get("meaning_cn") or ""), word)
    cat = category(word, book_id, entry)
    frames = FRAME_BANK.get(book_id) or FRAME_BANK["daily_life_ngsl"]
    if cat == "technical":
        frames = FRAME_BANK["computer_science"]
    elif book_id == "daily_life_ngsl" and cat in {"communication", "errand", "people", "health", "money", "weather", "object", "material", "time"}:
        frames = FRAME_BANK["daily_life_ngsl"]
    seed = f"{book_id}:{cat}:{word}"
    result: list[tuple[str, str, str, str]] = []
    used_frames: set[str] = set()
    for idx in range(max(12, len(frames) * 2)):
        level, scene, en, cn = _pick(frames, seed, idx)
        frame_key = f"{level}:{scene}:{en}"
        if frame_key in used_frames and len(used_frames) < len(frames):
            continue
        used_frames.add(frame_key)
        detail, detail_cn = _details(book_id, cat, seed, idx)
        values = {
            **_subject(seed, idx),
            "word": word,
            "meaning": meaning,
            "detail": detail,
            "detail_cn": detail_cn,
        }
        result.append((level, scene, _format_frame(en, values), _format_frame(cn, values)))
    return result


def audit(example: str, word: str) -> tuple[bool, float, list[str]]:
    text = re.sub(r"\s+", " ", example.strip())
    lower = text.lower()
    reasons: list[str] = []
    if word not in set(re.findall(r"[a-z]+(?:'[a-z]+)?", lower)):
        reasons.append("missing_target_word")
    if len(re.findall(r"[a-z]+(?:'[a-z]+)?", lower)) < 5:
        reasons.append("too_short")
    if len(re.findall(r"[a-z]+(?:'[a-z]+)?", lower)) > 18:
        reasons.append("too_long")
    if any(fragment in lower for fragment in BAD_FRAGMENTS):
        reasons.append("bad_fragment")
    if not text[:1].isupper() or text[-1:] not in ".!?":
        reasons.append("grammar_shape")
    score = 1.0 - min(0.5, 0.12 * len(reasons))
    return not reasons, round(score, 3), reasons


def signature(example: str, word: str) -> str:
    tokens = re.findall(r"[a-z]+(?:'[a-z]+)?", example.lower())
    out: list[str] = []
    for token in tokens:
        if token == word:
            out.append("{word}")
        elif token in {"i", "we", "they", "he", "she", "it", "someone"}:
            out.append("{subject}")
        elif token in {"the", "a", "an", "this", "that"}:
            out.append("{det}")
        else:
            out.append(token)
    return " ".join(out[:9])


def token_overlap(a: str, b: str) -> float:
    wa = set(re.findall(r"[a-z]+(?:'[a-z]+)?", a.lower()))
    wb = set(re.findall(r"[a-z]+(?:'[a-z]+)?", b.lower()))
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / max(1, len(wa | wb))


def candidate_pool(word: str, book_id: str, entry: dict[str, Any] | None) -> list[tuple[str, str, str, str]]:
    cat = category(word, book_id, entry)
    if word in CURATED_WORDS or cat in TEMPLATE_CATEGORIES:
        raw = templates(word, book_id, entry)
    else:
        raw = semantic_blueprints(word, book_id, entry)
    seen: set[str] = set()
    out: list[tuple[str, str, str, str]] = []
    for item in raw:
        key = re.sub(r"\s+", " ", item[2].strip().lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def select_diverse_examples(
    candidates: list[tuple[str, str, str, str]],
    word: str,
    limit: int = 5,
) -> list[tuple[int, str, str, str, str, float, list[str]]]:
    scored: list[tuple[int, str, str, str, str, float, list[str]]] = []
    for idx, (level, scene, example, example_cn) in enumerate(candidates, start=1):
        ok, score, reasons = audit(example, word)
        if not ok:
            continue
        scored.append((idx, level, scene, example, example_cn, score, reasons))
    picked: list[tuple[int, str, str, str, str, float, list[str]]] = []
    used_scenes: set[str] = set()
    used_signatures: set[str] = set()
    for item in scored:
        _, _, scene, example, _, _, _ = item
        sig = signature(example, word)
        if scene in used_scenes and len(used_scenes) < limit:
            continue
        if sig in used_signatures:
            continue
        if any(token_overlap(example, prev[3]) >= 0.58 for prev in picked):
            continue
        picked.append(item)
        used_scenes.add(scene)
        used_signatures.add(sig)
        if len(picked) >= limit:
            return picked
    for item in scored:
        if item in picked:
            continue
        sig = signature(item[3], word)
        if sig in used_signatures:
            continue
        if any(token_overlap(item[3], prev[3]) >= 0.72 for prev in picked):
            continue
        picked.append(item)
        used_signatures.add(sig)
        if len(picked) >= limit:
            return picked
    return picked[:limit]


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _dashscope_config() -> tuple[str, str, str]:
    _load_env_file(ROOT / ".env")
    key = (
        os.environ.get("DASHSCOPE_API_KEY_CN")
        or os.environ.get("DASHSCOPE_API_KEY")
        or os.environ.get("QWEN_API_KEY")
        or ""
    ).strip()
    base = (
        os.environ.get("DASHSCOPE_API_BASE_CN")
        or os.environ.get("DASHSCOPE_API_BASE")
        or DASHSCOPE_DEFAULT_BASE
    ).strip().rstrip("/")
    model = (os.environ.get("ENGLISH_EXAMPLE_PACK_MODEL") or DASHSCOPE_DEFAULT_MODEL).strip()
    return key, base, model


def _extract_json_object(text: str) -> Any:
    stripped = str(text or "").strip()
    start_obj = stripped.find("{")
    end_obj = stripped.rfind("}")
    start_arr = stripped.find("[")
    end_arr = stripped.rfind("]")
    if start_arr >= 0 and end_arr > start_arr and (start_obj < 0 or start_arr < start_obj):
        return json.loads(stripped[start_arr : end_arr + 1])
    if start_obj >= 0 and end_obj > start_obj:
        return json.loads(stripped[start_obj : end_obj + 1])
    return json.loads(stripped)


def dashscope_candidates(
    word: str,
    book_id: str,
    entry: dict[str, Any] | None,
    timeout_sec: int = 45,
) -> list[tuple[str, str, str, str]]:
    key, base, model = _dashscope_config()
    if not key:
        return []
    meaning = meaning_head(str((entry or {}).get("meaning_cn") or ""), word)
    scene = BOOK_SCENE.get(book_id, "natural English")
    prompt = (
        "你是英语学习 App 的例句主编。请为目标单词生成 8 条自然英文例句，并给出中文翻译。\n"
        "要求：每条例句必须包含目标单词原形；6-16 个英文词；不要模板感；不要元语言；不要解释这个词；"
        "场景要多样，有生活细节；英语必须像母语者会说的话。\n"
        f"目标单词: {word}\n"
        f"中文义项: {meaning}\n"
        f"词书/场景: {scene}\n"
        "只返回 JSON 数组，每项格式："
        "{\"level\":\"A2|B1|B2\",\"scene\":\"短英文场景名\",\"example\":\"英文例句\",\"example_cn\":\"中文翻译\"}"
    )
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Return strict JSON only. No markdown."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.75,
        "top_p": 0.9,
        "max_tokens": 1200,
    }
    request = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_sec) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError):
        return []
    try:
        payload = json.loads(raw)
        content = payload["choices"][0]["message"]["content"]
        parsed = _extract_json_object(content)
    except Exception:
        return []
    if isinstance(parsed, dict):
        parsed = parsed.get("examples") or parsed.get("items") or []
    if not isinstance(parsed, list):
        return []
    out: list[tuple[str, str, str, str]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        level = str(item.get("level") or "B1").strip() or "B1"
        scene = str(item.get("scene") or "model").strip() or "model"
        example = re.sub(r"\s+", " ", str(item.get("example") or "").strip())
        example_cn = re.sub(r"\s+", " ", str(item.get("example_cn") or "").strip())
        if example and example_cn:
            out.append((level, scene, example, example_cn))
    return out


def generate_candidates(
    word: str,
    book_id: str,
    entry: dict[str, Any] | None,
    generator: str,
) -> tuple[list[tuple[str, str, str, str]], str]:
    mode = (generator or "blueprint").strip().lower()
    if mode in {"dashscope", "model", "hybrid"}:
        model_items = dashscope_candidates(word, book_id, entry)
        if model_items:
            return model_items + candidate_pool(word, book_id, entry), "dashscope_reviewed"
        if mode in {"dashscope", "model"}:
            return candidate_pool(word, book_id, entry), "dashscope_failed_blueprint_fallback"
    return candidate_pool(word, book_id, entry), "semantic_blueprint"


def _task_list(
    wordbooks: dict[str, list[str]],
    limit: int | None = None,
    max_words: int | None = None,
) -> list[tuple[str, str]]:
    tasks: list[tuple[str, str]] = []
    for book_id, words in wordbooks.items():
        for word in words[: limit or len(words)]:
            clean = normalize_word(word) or clean_word(word)
            if not clean:
                continue
            tasks.append((book_id, clean))
            if max_words and len(tasks) >= max_words:
                return tasks
    return tasks


def _processed_key(book_id: str, word: str) -> str:
    return f"{book_id}:{word}"


def _load_checkpoint(path: Path) -> tuple[dict[str, list[dict[str, Any]]], set[str]]:
    if not path.is_file():
        return {}, set()
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}, set()
    items = raw.get("items") if isinstance(raw, dict) else {}
    processed = raw.get("processed") if isinstance(raw, dict) else []
    if not isinstance(items, dict):
        items = {}
    clean_items: dict[str, list[dict[str, Any]]] = {}
    for word, variants in items.items():
        if isinstance(variants, list):
            clean_items[str(word)] = [item for item in variants if isinstance(item, dict)]
    clean_processed = {str(item) for item in processed if isinstance(item, str)}
    return clean_items, clean_processed


def _write_checkpoint(
    path: Path | None,
    payload: dict[str, Any],
    processed: set[str],
) -> None:
    if not path:
        return
    checkpoint = {
        **payload,
        "processed": sorted(processed),
        "checkpoint_updated_at": int(time.time()),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _pack_payload(items: dict[str, list[dict[str, Any]]], total: int, generator: str) -> dict[str, Any]:
    return {
        "version": 1,
        "asset": "english_example_pack",
        "source": {
            "wordbook": str(DEFAULT_WORDBOOK_TS.relative_to(ROOT)).replace("\\", "/"),
            "generator": "scripts/generate_english_example_pack.py",
            "mode": f"{generator}_generated_reviewed_pack",
        },
        "counts": {"words": len(items), "examples": total},
        "items": items,
    }


def build_pack(
    wordbooks: dict[str, list[str]],
    limit: int | None = None,
    generator: str = "blueprint",
    *,
    checkpoint: Path | None = None,
    resume: bool = False,
    progress_every: int = 0,
    max_words: int | None = None,
) -> dict[str, Any]:
    tasks = _task_list(wordbooks, limit=limit, max_words=max_words)
    items: dict[str, list[dict[str, Any]]] = {}
    total = 0
    seen_global: set[str] = set()
    processed: set[str] = set()
    if resume and checkpoint:
        items, processed = _load_checkpoint(checkpoint)
        for variants in items.values():
            for item in variants:
                example = str(item.get("example") or "").strip().lower()
                if example:
                    seen_global.add(re.sub(r"\s+", " ", example))
        total = sum(len(v) for v in items.values())
        if processed:
            print(
                f"[ExamplePack] resume checkpoint={checkpoint} processed={len(processed)} words={len(items)} examples={total}",
                flush=True,
            )

    mode = (generator or "blueprint").strip().lower()
    if progress_every <= 0:
        progress_every = 1 if mode in {"hybrid", "dashscope", "model"} else 50

    started = time.time()
    task_count = len(tasks)
    done_count = sum(1 for book_id, word in tasks if _processed_key(book_id, word) in processed)
    print(
        f"[ExamplePack] start mode={generator} tasks={task_count} resume_done={done_count} checkpoint={checkpoint or '-'}",
        flush=True,
    )

    for ordinal, (book_id, clean) in enumerate(tasks, start=1):
        key = _processed_key(book_id, clean)
        if key in processed:
            continue
        word_started = time.time()
        if progress_every == 1:
            print(f"[ExamplePack] {ordinal}/{task_count} {book_id}:{clean} generating...", flush=True)
        entry = lookup_word(clean) or {}
        variants: list[dict[str, Any]] = []
        candidates, generator_source = generate_candidates(clean, book_id, entry, generator)
        selected = select_diverse_examples(candidates, clean, limit=5)
        for idx, level, scene, example, example_cn, score, reasons in selected:
            dedupe_key = re.sub(r"\s+", " ", example.strip().lower())
            if dedupe_key in seen_global:
                continue
            seen_global.add(dedupe_key)
            variants.append(
                {
                    "id": f"{book_id}:{clean}:{idx}",
                    "word": clean,
                    "book_id": book_id,
                    "scene": scene,
                    "level": level,
                    "sense_cn": str((entry or {}).get("meaning_cn") or ""),
                    "example": example,
                    "example_cn": example_cn,
                    "quality_score": score,
                    "grammar_score": score,
                    "naturalness_score": score,
                    "semantic_score": score,
                    "review_status": "approved",
                    "reviewer": "rule_critic_v1",
                    "generator": f"example_pack_pipeline_v2:{generator_source}",
                    "audit_reasons": reasons,
                }
            )
        if variants:
            items.setdefault(clean, []).extend(variants[:5])
            total += len(variants[:5])
        processed.add(key)
        done_count += 1
        payload = _pack_payload(items, total, generator)
        _write_checkpoint(checkpoint, payload, processed)
        if done_count % progress_every == 0 or done_count == task_count or progress_every == 1:
            elapsed = time.time() - started
            per_word = elapsed / max(1, done_count)
            eta = per_word * max(0, task_count - done_count)
            print(
                "[ExamplePack] "
                f"done={done_count}/{task_count} word={clean} source={generator_source} "
                f"selected={len(variants[:5])} elapsed={elapsed:.1f}s eta={eta:.1f}s "
                f"word_ms={int((time.time() - word_started) * 1000)}",
                flush=True,
            )
    return _pack_payload(items, total, generator)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate reviewed English example pack for local runtime lookup.")
    parser.add_argument("--wordbook-ts", type=Path, default=DEFAULT_WORDBOOK_TS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--limit-per-book", type=int, default=0, help="Debug limit. 0 means all words.")
    parser.add_argument("--max-words", type=int, default=0, help="Stop after this many total words across all books. 0 means all words.")
    parser.add_argument("--resume", action="store_true", help="Resume from checkpoint instead of starting from scratch.")
    parser.add_argument("--checkpoint", type=Path, default=None, help="Checkpoint path. Default: <out>.checkpoint.json")
    parser.add_argument("--progress-every", type=int, default=0, help="Print progress every N processed words. 0 chooses a sensible default.")
    parser.add_argument(
        "--generator",
        choices=["blueprint", "hybrid", "dashscope", "model"],
        default="blueprint",
        help="blueprint is fast/offline; dashscope/model uses Qwen compatible API first and falls back only when needed.",
    )
    args = parser.parse_args()
    wordbooks = extract_wordbooks(args.wordbook_ts)
    checkpoint = args.checkpoint or args.out.with_suffix(args.out.suffix + ".checkpoint.json")
    payload = build_pack(
        wordbooks,
        limit=args.limit_per_book or None,
        generator=args.generator,
        checkpoint=checkpoint,
        resume=args.resume,
        progress_every=args.progress_every,
        max_words=args.max_words or None,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("[ExamplePack] final " + json.dumps(payload["counts"], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
