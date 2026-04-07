"""
SQLite / MCP 数据问数：ReAct SOP、同链逻辑自检、双模型 Critic 预留说明。

与 context_sniffer 注入的 db_semantics.md、golden_sql_examples.jsonl 配合使用。
"""
from __future__ import annotations

SQLITE_REACT_SOP_BLOCK = """【数据库 ReAct·SOP】当用户问题依赖 workspace 内 SQLite（工具含 read_query / write_query 或 mcp:*sqlite*）时，禁止「一句自然语言 → 一条终极 SQL」单次直出。

须按显式多轮执行（可压缩在同一用户任务内、多轮 Thought/Action）：
1) **Probe（探表）**：面对生疏库或复杂问法时，先用 **read_query** 做只读探查，例如
   `SELECT name, sql FROM sqlite_master WHERE type='table';` 或 `PRAGMA table_info(表名);`，
   确认真实表名、列名与类型；勿臆造列名。
2) **Map（映射）**：在 Thought 中写明——用户用语（如「缺货」「低库存」）如何落到 **db_semantics.md**（若 [ENVIRONMENT_REPORT] 已注入）中的条件；
   若报告中无定义，须明确写出你采用的业务定义（例如缺货 = quantity=0）及对应列名，不得默认。
3) **Execute（执行）**：再发 **read_query** 执行最终 SELECT；**写操作**须先经参谋长签批流程，并在 JSON 中带 `jachin_mcp_write_ack`: true。

禁止跳过 Probe 直接盲写依赖列名的复杂 WHERE（除非同一会话已确认过表结构且用户仅追问衍生问题）。"""

SQLITE_SELF_CRITIC_BLOCK = """【SQL·逻辑自检（Actor 同链 Critic）】在给出用户可见的 **Final Answer** 之前，必须增加简短 **「【逻辑自检】」** 段落（建议 2～4 条要点），至少覆盖：
- 用户问的是谁/什么范围？当前 SQL 的 **WHERE / JOIN** 是否与之对应（未误用全表 SELECT * 后口述）？
- 结论中的每一项（如「谁缺货」）是否 **都能** 在本轮 **Observation 行** 中找到依据？数值是否与单元格一致？
- 若用户点名某一实体（如「香蕉」），结论是否 **错误归因** 到其他行？

若任一条明显不成立，须 **修正 SQL 再查** 或明确说「当前结果无法支持结论」，**禁止** 将矛盾结论当最终答案输出。

（完整 **双模型 Critic**：可将「用户原问 + SQL + Observation」二次送轻量模型裁决；见下方架构说明。当前默认用本段在同链内消化。）"""

SQLITE_ACTOR_CRITIC_STUB_NOTE = """【架构预留 · 双 Agent Critic】未来可经配置启用独立 Critic completion（如 `JACHIN_DB_CRITIC_ENABLED`），
在 Actor 拿到 SQL 与结果后、对用户可见前自动跑一轮校验；当前以 **【SQL·逻辑自检】** 段落在同一 ReAct 链内实现「Actor-Critic」效果，避免额外时延与费用。"""

SQLITE_REACT_SOP_BLOCK_SLIM = (
    "【DB·SOP】探表（sqlite_master/PRAGMA）→ 对照 db_semantics 映射 → 再 read_query；"
    "Final 前须「【逻辑自检】」。写库仍须签批 + jachin_mcp_write_ack。\n"
)
