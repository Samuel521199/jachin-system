"""
MemoryManager - 记忆协调中枢

战役二：工作记忆与大模型挂载
- 短期记忆 (STM)：滑动窗口，最近 10 轮对话
- 长期记忆 (LTM)：LanceDB RAG 检索，优先 Core Memory

战役三：梦境机制与记忆凝结
- 快路径：remember_core_fact 主动铭刻
- 慢路径：consolidate_memory 潜意识梦境提炼
"""

import asyncio
import logging
import threading
import time
from datetime import datetime
from typing import Any
from uuid import uuid4

from core.memory.chunk_schema import MemoryChunk
from core.memory.embedding import BaseEmbedder
from core.memory.store_protocol import VectorStoreProtocol

logger = logging.getLogger(__name__)

# 每轮 = 1 user + 1 assistant，10 轮 = 20 条消息
STM_MAX_MESSAGES = 20
# 梦境触发阈值：短期记忆达到此条数时触发 consolidate
CONSOLIDATION_THRESHOLD = 20
# 梦境最少条数：少于此时不提炼
CONSOLIDATION_MIN_MESSAGES = 3

DREAM_PROMPT = """以下是用户今天的对话记录。请提取出关于用户偏好、重要事件、习惯等有长期价值的事实，以简短的要点列表返回。
要求：
- 每条事实独立成行，格式如「用户喜欢喝冰美式」
- 排除无营养内容（如「你好」「在吗」「谢谢」）
- 最多返回 10 条
- 如果没有重要信息，请只返回「无」

对话记录：
"""


class MemoryManager:
    """
    记忆协调中枢

    连接短期记忆缓存与长期向量库，为 Commander 提供 RAG 上下文。
    """

    def __init__(
        self,
        store: VectorStoreProtocol,
        embedder: BaseEmbedder,
        stm_max_messages: int = STM_MAX_MESSAGES,
    ):
        """
        Args:
            store: 长期记忆向量库（LanceDB 等）
            embedder: 文本向量化引擎
            stm_max_messages: 短期记忆最大消息数（滑动窗口）
        """
        self.store = store
        self.embedder = embedder
        self.stm_max_messages = stm_max_messages
        self._stm_cache: dict[str, list[dict[str, str]]] = {}
        self._lock = threading.Lock()

    def add_dialogue(self, user_id: str, role: str, content: str) -> None:
        """
        将一轮对话追加到短期记忆缓存。

        Args:
            user_id: 用户 ID，用于多用户隔离
            role: "user" 或 "assistant"
            content: 消息内容
        """
        uid = user_id or "default"
        if not content.strip():
            return
        with self._lock:
            if uid not in self._stm_cache:
                self._stm_cache[uid] = []
            self._stm_cache[uid].append({"role": role, "content": content})
            # 滑动窗口：保留最近 N 条
            if len(self._stm_cache[uid]) > self.stm_max_messages:
                self._stm_cache[uid] = self._stm_cache[uid][-self.stm_max_messages :]

    def get_short_term_messages(self, user_id: str) -> list[dict[str, str]]:
        """
        获取该用户的短期记忆（最近几轮对话）。

        Returns:
            [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}, ...]
        """
        uid = user_id or "default"
        with self._lock:
            return list(self._stm_cache.get(uid, []))

    def retrieve_context(
        self,
        query: str,
        user_id: str,
        limit: int = 5,
        character_id: str = "",
        device_id: str = "",
    ) -> str:
        """
        RAG 核心：从长期记忆中检索与 query 相关的往事。

        - 将 query 向量化，在 store 中按 user_id 过滤检索
        - 优先将 is_core=True（铂金记忆）排在最前面
        - 拼接成可注入 System Prompt 的字符串

        Args:
            query: 用户当前提问
            user_id: 用户 ID，用于过滤
            limit: 检索数量上限
            character_id: 人格 ID（可选）
            device_id: 设备 ID（可选）

        Returns:
            格式化的记忆字符串，如 "【已知相关记忆】：\n- 主人喜欢吃香蕉 (记于 2026-02-27)\n..."
            若无结果或发生异常，返回空字符串
        """
        try:
            vector = self.embedder.embed_text(query)
        except Exception as e:
            logger.error("MemoryManager: Embedding 失败，跳过 LTM 检索: %s", e)
            return ""

        filter_dict: dict[str, Any] = {"user_id": user_id or "default"}
        if character_id:
            filter_dict["character_id"] = character_id
        if device_id:
            filter_dict["device_id"] = device_id

        try:
            chunks = self.store.search(
                query_vector=vector,
                limit=limit * 2,  # 多取一些，便于排序后截断
                filter_dict=filter_dict,
            )
        except Exception as e:
            logger.error("MemoryManager: LTM 检索失败，跳过: %s", e)
            return ""

        if not chunks:
            return ""

        # 优先 is_core=True 排前面
        core_first = sorted(chunks, key=lambda c: (not c.is_core, -c.timestamp))
        core_first = core_first[:limit]

        lines = []
        for c in core_first:
            try:
                ts_str = datetime.fromtimestamp(c.timestamp).strftime("%Y-%m-%d") if c.timestamp else "未知"
                tag = " [核心记忆]" if c.is_core else ""
                lines.append(f"- {c.content}{tag} (记于 {ts_str})")
            except Exception:
                continue

        if not lines:
            return ""
        return "【已知相关记忆】：\n" + "\n".join(lines)

    def remember_core_fact(
        self,
        fact: str,
        user_id: str,
        character_id: str = "",
        device_id: str = "",
    ) -> None:
        """
        快路径：主动铭刻核心记忆（铂金标签）。

        当用户明确下令「记住 xxx」时，由 Commander 的 remember_core_fact 工具调用。
        """
        if not fact.strip():
            return
        try:
            vector = self.embedder.embed_text(fact.strip())
        except Exception as e:
            logger.error("remember_core_fact: Embedding 失败: %s", e)
            raise
        chunk = MemoryChunk(
            id=str(uuid4()),
            content=fact.strip(),
            vector=vector,
            user_id=user_id or "default",
            device_id=device_id,
            character_id=character_id,
            is_core=True,
            timestamp=int(time.time()),
        )
        self.store.upsert([chunk])
        logger.info("Core memory 已铭刻: user=%s, fact=%s...", user_id, fact[:50])

    def get_stm_count(self, user_id: str) -> int:
        """获取该用户短期记忆条数"""
        uid = user_id or "default"
        with self._lock:
            return len(self._stm_cache.get(uid, []))

    async def consolidate_memory(
        self,
        user_id: str,
        llm_client: Any,
        model: str = "gpt-4o-mini",
    ) -> None:
        """
        慢路径：梦境提炼 - 将短期记忆交给 LLM 摘要，写入长期记忆后清空 STM。

        Args:
            user_id: 用户 ID
            llm_client: AsyncOpenAI 兼容客户端
            model: 模型名称
        """
        uid = user_id or "default"
        with self._lock:
            messages = list(self._stm_cache.get(uid, []))

        if len(messages) < CONSOLIDATION_MIN_MESSAGES:
            return

        # 构造对话文本
        dialogue_text = "\n".join(
            f"{m['role']}: {m['content']}" for m in messages
        )

        try:
            resp = await llm_client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "你是一个记忆提炼助手，从对话中提取有长期价值的事实。"},
                    {"role": "user", "content": DREAM_PROMPT + dialogue_text},
                ],
                temperature=0.3,
                max_tokens=512,
            )
            content = ""
            if resp.choices:
                content = (getattr(resp.choices[0].message, "content", None) or "").strip()
        except Exception as e:
            logger.error("consolidate_memory: LLM 调用失败: %s", e)
            return

        if not content or "无" in content[:20]:
            with self._lock:
                self._stm_cache[uid] = []
            logger.info("梦境提炼: 无重要信息，已清空 STM")
            return

        # 按行解析事实，过滤空行和「无」
        lines = [l.strip() for l in content.split("\n") if l.strip() and "无" not in l[:5]]
        if not lines:
            with self._lock:
                self._stm_cache[uid] = []
            return

        ts = int(time.time())
        chunks = []
        for line in lines[:10]:
            try:
                vector = self.embedder.embed_text(line)
                chunks.append(MemoryChunk(
                    id=str(uuid4()),
                    content=line,
                    vector=vector,
                    user_id=uid,
                    device_id="",
                    character_id="",
                    is_core=False,
                    timestamp=ts,
                ))
            except Exception as e:
                logger.warning("consolidate_memory: Embedding 失败 %s: %s", line[:30], e)

        if chunks:
            try:
                self.store.upsert(chunks)
                logger.info("梦境提炼: 写入 %d 条长期记忆", len(chunks))
            except Exception as e:
                logger.error("consolidate_memory: 写入失败: %s", e)
                return

        with self._lock:
            self._stm_cache[uid] = []

