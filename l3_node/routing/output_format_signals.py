"""
检测用户消息中的「格式强约束」与直连 LLM 可行性。
用于：动态瘦身系统提示词、在无需工具时绕过 ReAct。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass


# 明显需要走工具链的意图（命中则禁止直连）
_TOOL_NEED_RE = re.compile(
    r"读取|读一下|打开.{0,4}文件|fs_read|list_dir|list_directory|shell_exec|执行命令|跑一下|运行脚本|"
    r"grep|rg\s|搜索代码|apply_patch|写文件|fs_write|submit_background_task|check_background_task|"
    r"delegate|coordinate|recall_memory|local_memory_search|"
    r"atom_post|add_automated|透析镜|mcp:|jpp:com\.jachin|"
    # PPTX / Office MCP：避免走 direct_llm_bypass 后模型编造「无法连接 MCP」；须进 ReAct 调 mcp:create_presentation 等
    r"pptx?|powerpoint|幻灯片|演示文稿|\.pptx|"
    r"create_presentation|save_presentation|add_slide|populate_placeholder|"
    r"(?:用|通过|调用)\s*MCP|MCP\s*(?:新建|创建|生成|做)|"
    # 实况天气需 util:get_weather_lite；否则易走 direct_llm_bypass 编造「服务不可用」
    r"(?:查|看|问|说说).{0,4}天气|天气.{0,16}(?:怎么样|如何|多少|冷不冷)|"
    r"(?:今日|今天|明天|当地|外面).{0,12}天气|天气预报|气温|下雨|下雪|台风|空气质量|AQI|"
    r"weather\s+in|what.{0,28}weather|util:get_weather_lite|"
    # 原生 Office / 本机落盘：直连模式下模型会「假装」执行 Python/code_interpreter，文件不会真写入；须走 ReAct 调 util:generate_office_doc 等
    r"util:generate_office_doc|openpyxl|python-docx|code_interpreter|"
    r"\.xlsx|\.docx|\.xlsm?\b|excel|工作表|工作簿|预算表|"
    r"保存.{0,32}(?:到|在|至).{0,16}(?:桌面|电脑|本机)|"
    r"(?:桌面|Downloads|Documents|下载|文档)(?:路径|文件夹|目录)?|"
    r"(?:生成|导出|写出|另存).{0,24}(?:word|excel|xlsx|docx|表格|文档|报告)|"
    r"(?:word|excel).{0,12}(?:报告|文档|表格|文件)|"
    # 含 URL / 明确抓网页：须走 mcp:fetch、stealth 等；禁止直连否则只输出「Action:」假动作、不执行工具（见 terminal_turn 只有流式无 Observation）
    r"https?://|抓取|抓网页|爬取|爬虫|下载网页|网页内容",
    re.I,
)

# 强格式接管：用户明确要求不要套话 / 仅结构化输出等（正文中避免出现井号标题样例，以免模型照抄）
_USER_LED_STRICT_RE = re.compile(
    r"(?:禁止|不要|严禁|不得|绝不能|请勿).{0,48}(?:问候|寒暄|开场白|思考过程|Thought|Final\s*Answer|结束语|套话|markdown\s*标题)"
    r"|(?:只要|仅|只|必须).{0,32}(?:输出|回复|返回).{0,24}(?:json|JSON|`\s*json|合法\s*json)"
    r"|(?:仅|只|必须).{0,20}(?:以|从)\s*[\{\[]"
    r"|必须且只能"
    r"|json[_\s-]*object|response_format|\"type\"\s*:\s*\"json"
    r"|纯粹的?\s*JSON\s*API|机器可读|不要.*markdown"
    r"|只能以\s*[\{\[]",
    re.I | re.S,
)

_JSON_OBJECT_HINT_RE = re.compile(
    r"json|以\s*[\{｛]\s*开头|schema|键值对|合法\s*json|```json",
    re.I,
)

# 轻量「要 JSON」表述：无严厉禁止套话时也允许直连 + json_object（仍禁止工具意图）
# 注意：json 后常接中文（如「json结果」），勿用 \b 结尾，否则 Unicode 下边界不成立
_JSON_RELAXED = re.compile(
    r"(?:请|帮)?(?:我)?(?:输出|给出|返回|生成|整理成?|转为).{0,36}(?:合法)?\s*json|"
    r"(?:输出|给出|返回|生成)\s*json|"
    r"(?:只要|仅|只)(?:输出|给|返回).{0,16}json|"
    r"json\s*(?:格式|输出|结果|即可)|"
    r"json结果|"
    r"(?:以|用)\s*json\s*(?:回答|输出)?",
    re.I | re.S,
)


@dataclass(frozen=True)
class OutputFormatSignals:
    """本轮输出格式信号。"""

    user_led_strict: bool
    """用户是否显式要求接管输出形态（问候/ReAct 套话/仅 JSON 等）。"""

    prefer_json_object: bool
    """是否建议对 API 使用 json_object（直连模式，需与 user_led 或 json 意图同时成立时由调用方解释）。"""

    json_relaxed: bool
    """较轻的「请输出 JSON」类意图（无严厉禁止套话）。"""

    def slim_system_prompt(self) -> bool:
        return self.user_led_strict or self.json_relaxed or self.prefer_json_object


def _direct_bypass_disabled() -> bool:
    return os.environ.get("JACHIN_DISABLE_DIRECT_LLM_BYPASS", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def heuristic_tool_need(user_text: str) -> bool:
    return bool(_TOOL_NEED_RE.search(user_text or ""))


# 极短寒暄 / 礼貌用语：无任务语义时允许直连 completion，避免被长会话历史拖进 ReAct+大工具池
_TRIVIAL_CHITCHAT_BLOCK_RE = re.compile(
    r"读|写|文件|执行|命令|代码|http|www\.|toutiao|\.txt|\.py|mcp|工具|总结|抓取|覆盖|工作区|shell|powershell|curl|帮我|请帮",
    re.I,
)
_TRIVIAL_CHITCHAT_OK_RE = re.compile(
    r"^[\s，。！？、…·~～！]+$"
    r"|^[\s，。！？、…·~～！]*(?:"
    r"你好|您好|嗨|哈喽|哈啰|hello|hi|hey|早上好|下午好|晚上好|早安|午安|晚安|"
    r"谢谢|多谢|感谢|不客气|拜拜|再见|see\s*ya|在吗|在不在|有人吗|哈喽哈喽|"
    r"你好呀|你好啊|你好哦|在的|嗯嗯|好的|ok|okay|么么哒|辛苦|劳烦|打扰"
    r")[\s，。！？、…·~～！]*$",
    re.I,
)


def heuristic_trivial_chitchat_only(user_text: str) -> bool:
    """是否仅为简短问候/致谢等，不包含任何可识别的任务意图。"""
    t = (user_text or "").strip()
    if not t or len(t) > 48:
        return False
    if _TRIVIAL_CHITCHAT_BLOCK_RE.search(t):
        return False
    return bool(_TRIVIAL_CHITCHAT_OK_RE.match(t))


# 安全锁动态域：仅当用户话术中明显涉及 DB / Shell 时注入对应域文件，避免「CSS 任务却灌 10 万 token 安全锁」
_DB_SAFETY_LOCK_RE = re.compile(
    r"数据库|数据表|SQL|mysql|postgres|oracle|mongodb|redis|表名|schema|information_schema|"
    r"duckdb|sqlite|迁移|冗余日志|delete\s+from|drop\s+table|truncate|事务|主键|外键",
    re.I,
)
_SHELL_SAFETY_LOCK_RE = re.compile(
    r"shell_exec|执行命令|跑一下脚本|bash|powershell|cmd\.exe|kubectl|k8s|docker\s+(run|exec)|"
    r"systemd|cron|rm\s+-rf|删库|chmod\s+777",
    re.I,
)


def heuristic_safety_lock_domains(user_text: str) -> list[str]:
    """
    返回需按需挂载的安全锁域 id：db | shell。
    未命中时调用方应 **不** 注入大块全局安全锁（仅允许 pin / 极短 legacy 头）。
    """
    t = user_text or ""
    out: list[str] = []
    if _DB_SAFETY_LOCK_RE.search(t):
        out.append("db")
    if _SHELL_SAFETY_LOCK_RE.search(t):
        out.append("shell")
    return out


def analyze_output_format_signals(user_text: str) -> OutputFormatSignals:
    t = user_text or ""
    led = bool(_USER_LED_STRICT_RE.search(t))
    json_hint = bool(_JSON_OBJECT_HINT_RE.search(t))
    jr = bool(_JSON_RELAXED.search(t))
    return OutputFormatSignals(
        user_led_strict=led,
        prefer_json_object=led and json_hint,
        json_relaxed=jr,
    )


def should_use_direct_llm_bypass(
    user_text: str,
    *,
    delegate_depth: int = 0,
    channel: str = "",
    raw_user_input: str = "",
) -> tuple[bool, bool]:
    """
    返回 (use_direct, json_object_mode)。
    无工具意图时：强格式约束或轻量 JSON 请求均可直连；json_object 在可行时开启。
    raw_user_input：原始用户句；与分类面不一致时仍以原始句做 OOD 闸（防乱码夹带抠句绕过）。
    """
    if _direct_bypass_disabled():
        return False, False
    _raw_ui = (raw_user_input or "").strip() or (user_text or "").strip()
    # 网关分类面常拼接历史摘要（含 shell/mcp/读取 等），不得据此挡掉「你好」类纯寒暄直连
    _trivial_raw = heuristic_trivial_chitchat_only(_raw_ui)
    try:
        from l3_node.intent_gateway.ood_signals import evaluate_gateway_ood_gates

        _raw = _raw_ui
        _cls_for_ood = _raw_ui if _trivial_raw else (user_text or "")
        _og = evaluate_gateway_ood_gates(
            raw_user_input=_raw,
            classification_text=_cls_for_ood,
            bundle_extra=None,
        )
        if _og.veto_direct_bypass:
            return False, False
    except Exception:
        pass
    if delegate_depth > 0:
        return False, False
    ch = (channel or "").strip().lower()
    if ch in ("background_task", "delegate_sub_agent"):
        return False, False
    if _trivial_raw:
        return True, False
    # 分类面可能不含 URL（网关摘要截断）；原句含 http/抓取 时也必须禁止直连
    if heuristic_tool_need(user_text) or heuristic_tool_need(_raw_ui):
        return False, False
    if heuristic_trivial_chitchat_only(user_text):
        return True, False
    sig = analyze_output_format_signals(user_text)
    if sig.user_led_strict:
        return True, sig.prefer_json_object
    if sig.json_relaxed:
        return True, True
    return False, False
