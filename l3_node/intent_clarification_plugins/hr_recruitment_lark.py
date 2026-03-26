"""
HR 招聘 · 飞书 Lark 遥控：模糊说法 → 反问确认。

依赖 ``hr_lark_command_lexicon`` 排除已精确命中的句子；规则仅负责「差一点就命中」的补充检测。
"""

from __future__ import annotations

import re

from l3_node.hr_lark_command_lexicon import (
    ANALYZE_AMBIGUOUS,
    matches_continue_command,
    matches_hr_analyze_command,
    matches_hr_status_briefing_command,
    matches_stop_harvest_inject,
    recruitment_stop_without_harvest_cue,
)
from l3_node.intent_clarification import ClarificationRule

_RECRUITMENT_CONTEXT_FOR_FUZZY_ANALYZE = re.compile(
    r"简历|候选人|透析|琅琊|招聘|pending|boss|收网|入库|待透析|分析报告|排行榜|职位|岗位|hr|wasm",
    re.I,
)

_FUZZY_ANALYZE_HINT = re.compile(
    r"(要不要|能不能|可不可以|可否|帮忙|帮我|麻烦|想|需要|准备|打算|是不是该|是不是要).{0,12}(分析|透析|跑透析|琅琊榜|排行榜|评估|打分|过一遍)"
    r"|(简历|候选人|pending).{0,10}(分析|透析|评估|打分|过一遍|瞅瞅|瞧瞧)(?!.*(BI|报表|数据))"
    r"|分析.{0,8}(一下|下)?(简历|候选人|他们|这些人)"
    r"|(出|生成|跑|来|弄).{0,6}(个)?(琅琊榜|排行榜|透析报告?)"
    r"|透析.{0,8}(一下|下|呗)?"
    r"|看看.{0,6}简历.{0,10}(咋样|怎么样|匹配|合不合适|行不行)"
    r"|wasm|透析镜.{0,6}(跑|开|搞|下)",
    re.I,
)
_FUZZY_CONTINUE_HINT = re.compile(
    r"^(接着|继续|恢复).{0,10}(抓|收|收网|捞|干活|跑|弄|搞|整)(?!.*分析)"
    r"|^(再|接着).{0,6}(抓|收|收网|捞)(?!.*分析)"
    r"接着招|继续招|恢复招聘|接着弄招聘",
    re.I,
)
_FUZZY_STOP_HINT = re.compile(
    r"(先|能不能|麻烦|帮忙).{0,6}(停|别).{0,8}(抓|收|收网|下载|跑)"
    r"|别.{0,6}(抓|收|收网|下载)了"
    r"|不要再抓|先别抓|缓缓先别抓|停一停.{0,4}抓",
    re.I,
)
_FUZZY_STATUS_HINT = re.compile(
    r"^(怎么样了|咋样了|如何了|还行吗|顺利吗)[？?！!。…]?$"
    r"|^(怎么样|如何|咋样|行不行|顺不顺).{0,6}[了啦吗呢呀哇]?[？?！!。…]?$"
    r"|进行到哪|到哪步|什么状况|还好吗|卡住|卡住了|怎么还没|还没好"
    r"|招聘.{0,8}(进度|状态|情况|咋样)",
    re.I,
)
_FUZZY_CONSENT_ACTION = re.compile(
    r"^(好|行|可以|中|妥|没问题|OK|ok|嗯|噢|哦).{1,28}(分析|透析|抓|收网|继续|启动|开干|弄|跑|搞)"
    # 「确认启动/同意启动」由 lark_workflow_command_interceptor 整行匹配启动调度，勿当模糊同意
    r"|^(同意|确认)(?!启动).{0,16}(吧|的)?[，,]?\s*(启动|开始|分析|透析|抓|收|继续)"
    r"|那就(分析|透析|抓|收|继续|启动)",
    re.I,
)


def hr_recruitment_lark_clarification_rules() -> list[ClarificationRule]:
    def _t_analyze(t: str) -> bool:
        if ANALYZE_AMBIGUOUS.search(t):
            return False
        if matches_hr_analyze_command(t):
            return False
        if not _FUZZY_ANALYZE_HINT.search(t):
            return False
        return bool(_RECRUITMENT_CONTEXT_FOR_FUZZY_ANALYZE.search(t))

    def _t_continue(t: str) -> bool:
        return bool(_FUZZY_CONTINUE_HINT.search(t)) and not matches_continue_command(t)

    def _t_stop(t: str) -> bool:
        if matches_stop_harvest_inject(t):
            return False
        if recruitment_stop_without_harvest_cue(t):
            return False
        return bool(_FUZZY_STOP_HINT.search(t))

    def _t_status(t: str) -> bool:
        return bool(_FUZZY_STATUS_HINT.search(t)) and not matches_hr_status_briefing_command(t)

    def _t_consent(t: str) -> bool:
        return bool(_FUZZY_CONSENT_ACTION.search(t))

    return [
        ClarificationRule(
            rule_id="hr_lark:analyze",
            priority=10,
            test=_t_analyze,
            reply=(
                "💬 听起来您想**分析 / 透析**已入库简历并出琅琊榜？\n\n"
                "请回复 **「分析简历」**（或 **「开始分析」**）确认执行；若只想看数据不发 Wasm，请发 **「进度」**。\n"
                "若其实指 BI/报表分析，请说明具体报表名称，避免误触透析镜。"
            ),
        ),
        ClarificationRule(
            rule_id="hr_lark:continue",
            priority=20,
            test=_t_continue,
            reply=(
                "💬 听起来您想**接着收网 / 恢复无人值守**？\n\n"
                "请回复 **「继续」** 或 **「继续收网」** 确认；若其实想**启动透析**，请发 **「分析简历」**。"
            ),
        ),
        ClarificationRule(
            rule_id="hr_lark:stop_harvest",
            priority=30,
            test=_t_stop,
            reply=(
                "💬 听起来您想**先停收网**？\n\n"
                "请回复 **「停止」** 或 **「停止收网」** 立即停当前抓取；若需**关掉整个无人值守招聘**，请说明 **「停止招聘」** 或 **「关闭无人值守」**（将走调度器/助手）。"
            ),
        ),
        ClarificationRule(
            rule_id="hr_lark:status",
            priority=40,
            test=_t_status,
            reply=(
                "💬 听起来您在问**招聘进度 / 状态**？\n\n"
                "请回复 **「进度」** 或 **「招聘进度」** 获取与系统简报一致的汇总；也可发 **「什么进度」**。"
            ),
        ),
        ClarificationRule(
            rule_id="hr_lark:consent_action",
            priority=50,
            test=_t_consent,
            reply=(
                "💬 您这句像是**同意执行某项操作**，但系统需要**明确动作**才能遥控调度。\n\n"
                "请**择一**发送：\n"
                "· **分析简历** — 停收网并跑透析镜 / 琅琊榜\n"
                "· **继续** — 清除停止并恢复无人值守 tick\n"
                "· **停止** — 停当前收网\n"
                "· **进度** — 当前 pending/透析/调度概况\n"
                "若只是在闲聊确认，请忽略本条。"
            ),
        ),
    ]
