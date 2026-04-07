"""
数据库问数 · 独立 Critic 模型（预留）。

当前由 `prompt_sqlite_sop.SQLITE_SELF_CRITIC_BLOCK` 在同一 ReAct 链内完成「自检」；
若后续接入第二路 completion，可在此实现 `run_db_critic_async(user_q, sql, observation_text) -> verdict`。
"""
from __future__ import annotations

# 占位：供配置或特性开关探测
DB_CRITIC_FEATURE_FLAG = "JACHIN_DB_CRITIC_ENABLED"
