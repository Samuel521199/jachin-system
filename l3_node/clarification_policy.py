"""Clarification policy for mission routing.

Ask at most one high-value question. If a slot can be resolved by project
memory or defaults, the executor should do that instead of asking.
"""
from __future__ import annotations

from l3_node.mission_intent_schema import CapabilityRoute, ClarificationDecision, MissionIntent, MissionTaskType


def decide_clarification(intent: MissionIntent, route: CapabilityRoute) -> ClarificationDecision:
    if intent.task_type == MissionTaskType.UNKNOWN:
        return ClarificationDecision(False)

    if "project" in intent.missing_slots and intent.task_type == MissionTaskType.PROJECT_BRIEFING_DELIVERY:
        return ClarificationDecision(
            True,
            "要总结哪个项目？可以直接说项目名，比如 Jachin；如果我还没记住路径，也可以给我项目路径。",
            "missing_project",
        )

    if "recipients" in intent.missing_slots and intent.task_type in {
        MissionTaskType.PROJECT_BRIEFING_DELIVERY,
        MissionTaskType.LARK_MESSAGE_SEND,
    }:
        return ClarificationDecision(True, "要发送给谁？可以是一个人、多人，或群聊名称。", "missing_recipients")

    if "project_name" in intent.missing_slots and intent.task_type == MissionTaskType.PROJECT_MEMORY_UPDATE:
        return ClarificationDecision(True, "要记住哪个项目名？例如：Jachin = D:\\Projects\\jachi\\jachin-system-main。", "missing_project_name")

    if "project_path" in intent.missing_slots and intent.task_type == MissionTaskType.PROJECT_MEMORY_UPDATE:
        return ClarificationDecision(True, "这个项目的本机路径是什么？", "missing_project_path")

    if "message" in intent.missing_slots and intent.task_type == MissionTaskType.LARK_MESSAGE_SEND:
        return ClarificationDecision(True, "要发送什么内容？", "missing_message")

    if "app_name" in intent.missing_slots and intent.task_type == MissionTaskType.APP_CONTROL:
        return ClarificationDecision(True, "要打开或切换到哪个 App？", "missing_app_name")

    if "file_path" in intent.missing_slots and intent.task_type == MissionTaskType.FILE_TO_APP:
        return ClarificationDecision(True, "要处理哪个文件？请给我文件名或路径。", "missing_file_path")

    if "app_name" in intent.missing_slots and intent.task_type == MissionTaskType.FILE_TO_APP:
        return ClarificationDecision(True, "要把文件发送或上传到哪个 App？例如 Lark、浏览器或邮件。", "missing_target_app")

    if not route.ok:
        return ClarificationDecision(
            True,
            f"我理解这是 {intent.task_type.value}，但当前缺少可用 workflow：{route.tool_id or route.reason}。",
            "route_unavailable",
        )

    if intent.confidence < 0.55:
        return ClarificationDecision(True, "我有点不确定你的目标，可以再说一下要操作什么对象、发给谁吗？", "low_confidence")

    return ClarificationDecision(False)
