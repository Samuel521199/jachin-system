"""Prompt policy snippets owned by the Cognitive Kernel.

The legacy text executor still consumes prompt snippets, but the policy source
must live outside ``agent_core.py`` so the main agent does not keep growing into
an architecture document.
"""

from __future__ import annotations

SQL_DATA_SOP_PROMPT = """【数据/数据库 Role Agent SOP】
当任务依赖数据库、MCP 数据表、业务语义字典或模糊业务词汇时，禁止直接编造最终 SQL、代码或结论。必须按 WorkOrder 证据链执行：

1. probe：先用只读工具探查真实表、字段、记录或文件结构。
2. map：基于 Observation 和业务语义字典说明字段映射，不能跳过证据。
3. execute：只在输入、权限和风险等级允许时执行读写动作。
4. continuous execution：如果用户要求“先查再改”，必须连续完成读、判断、写、校验，不能在只完成查询后 Final Answer。
5. proactive journaling：复杂跨会话任务完成前，应考虑写入 progress/task_plan/memory 证据，由对应 Role Agent 执行。
"""
