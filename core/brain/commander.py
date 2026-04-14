"""
Commander Agent - 智能意图路由与工具调度中枢

终局战役：唤醒司令官
- 接收用户文本，利用大模型 Function Calling 能力
- 从 PluginManager 获取沙箱插件的工具箱
- 路由到对应工具执行，生成拟人化回复
- 战役三：内置 remember_core_fact 工具，支持主动铭刻核心记忆
"""

import asyncio
import json
import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# 内置工具：核心记忆铭刻（战役三）
REMEMBER_CORE_FACT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "remember_core_fact",
        "description": "当用户明确要求记住某条重要信息时调用，如「记住我家的 Wi-Fi 密码是 1234」「记住我绝对不吃香菜」。将事实写入长期记忆并打上铂金标签，永不遗忘。",
        "parameters": {
            "type": "object",
            "properties": {
                "fact": {"type": "string", "description": "需要永久记住的核心事实，如「主人家的 Wi-Fi 密码是 1234」"},
            },
            "required": ["fact"],
        },
    },
}

# OpenAI 兼容客户端（支持 OpenAI / DeepSeek / 本地 vLLM 等）
try:
    from openai import AsyncOpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    AsyncOpenAI = None

SYSTEM_PROMPT_BASE = """你叫 Jachin，是主人的专属全息智能伴侣。
请根据用户的输入，选择合适的工具获取信息，并用简短、温柔、有感情的语言回复用户。
当用户明确要求「记住 xxx」时，务必调用 remember_core_fact 工具将事实写入长期记忆。
若无需调用工具，请直接以温柔的口吻回复。回复要自然、口语化，适合语音播报。"""

FALLBACK_SYSTEM_PROMPT_BASE = """你叫 Jachin，是主人的专属全息智能伴侣。
请用简短、温柔、有感情的语言回复用户。回复要自然、口语化，适合语音播报。"""


def _build_system_prompt(ltm_context: str) -> str:
    """融合长期记忆到 System Prompt"""
    if not ltm_context or not ltm_context.strip():
        return SYSTEM_PROMPT_BASE
    return (
        f"{SYSTEM_PROMPT_BASE}\n\n"
        f"以下是你大脑深处关于这位主人的记忆：\n{ltm_context}\n"
        "请结合这些记忆与用户对话。"
    )


def _capability_to_tool_schema(cap: Dict[str, Any]) -> Dict[str, Any]:
    """将插件的 capability 转为 OpenAI Function Calling 格式"""
    name = cap.get("name", "unknown")
    desc = cap.get("description", "无描述")
    params_schema = cap.get("parameters", {"type": "object", "properties": {}, "required": []})
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": params_schema if isinstance(params_schema, dict) else {"type": "object", "properties": {}, "required": []},
        },
    }


class CommanderAgent:
    """
    司令官 Agent - 语义路由与工具调度

    从 PluginManager 获取沙箱插件的工具箱，通过 LLM Function Calling 智能调度。
    """

    def __init__(
        self,
        plugin_manager: Any,
        api_base: Optional[str] = None,
        api_key: Optional[str] = None,
        model: str = "gpt-4o-mini",
        max_retries: int = 2,
        memory_manager: Optional[Any] = None,
    ):
        """
        Args:
            plugin_manager: PluginManager 实例，用于获取工具箱
            api_base: LLM API 基地址（OpenAI 兼容，如 https://api.openai.com 或本地 vLLM）
            api_key: API Key（本地模型可传 "none" 或空）
            model: 模型名称
            max_retries: 工具调用失败时的重试次数
            memory_manager: 记忆协调中枢（可选），用于 RAG 长短期记忆融合
        """
        self.plugin_manager = plugin_manager
        self.api_base = api_base
        self.api_key = api_key or "none"
        self.model = model
        self.max_retries = max_retries
        self.memory_manager = memory_manager
        self._client: Optional[Any] = None

    def _get_client(self) -> "AsyncOpenAI":
        if not OPENAI_AVAILABLE:
            raise ImportError("openai 包未安装。请执行: pip install openai")
        if self._client is None:
            kwargs: Dict[str, Any] = {"api_key": self.api_key}
            if self.api_base:
                kwargs["base_url"] = self.api_base
            self._client = AsyncOpenAI(**kwargs)
        return self._client

    def _get_tools_and_handlers(
        self, agent_context: Optional[Dict[str, Any]] = None
    ) -> tuple[List[Dict[str, Any]], Dict[str, Callable[..., Any]]]:
        """
        从 PluginManager 获取工具箱与执行器映射。
        战役三：内置 remember_core_fact 工具，始终优先添加。
        """
        tools: List[Dict[str, Any]] = []
        handlers: Dict[str, Callable[..., Any]] = {}

        ctx = agent_context or {}
        user_id = str(ctx.get("user_id", "default"))
        character_id = str(ctx.get("character_id", ""))
        device_id = str(ctx.get("device_id", ""))

        # 内置工具：核心记忆铭刻（战役三）
        if self.memory_manager:
            tools.append(REMEMBER_CORE_FACT_SCHEMA)
            mm = self.memory_manager
            def _remember_handler(params: dict):
                fact = (params.get("fact") or "").strip()
                if not fact:
                    return {"success": False, "text": "需要提供要记住的事实"}
                try:
                    mm.remember_core_fact(fact, user_id, character_id, device_id)
                    return {"success": True, "text": f"好的，已牢牢记住：{fact}"}
                except Exception as e:
                    logger.warning("remember_core_fact 失败: %s", e)
                    return {"success": False, "text": f"记忆写入失败: {e}"}
            handlers["remember_core_fact"] = _remember_handler

        sandbox_plugins = getattr(self.plugin_manager, "_sandbox_plugins", None) or {}

        for plugin_id, entry_point in sandbox_plugins.items():
            if not callable(entry_point):
                continue
            try:
                result = entry_point(ctx)
                if not isinstance(result, dict):
                    continue
                caps = result.get("capabilities", [])
                for cap in caps:
                    if not isinstance(cap, dict):
                        continue
                    name = cap.get("name")
                    handler = cap.get("handler")
                    if not name or not callable(handler):
                        continue
                    if name in handlers:
                        continue  # 同名能力保留第一个
                    schema = _capability_to_tool_schema(cap)
                    tools.append(schema)
                    handlers[name] = handler
            except Exception as e:
                logger.warning("Commander: 加载插件 %s 能力失败: %s", plugin_id, e)

        return tools, handlers

    async def process_request(
        self,
        user_text: str,
        agent_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        处理用户文本：路由决策 -> 工具执行 -> 拟人化回复。
        战役二：融合长短期记忆（RAG），让 Jachin 拥有回忆能力。
        """
        ctx = agent_context or {}
        user_id = str(ctx.get("user_id", "default"))
        character_id = str(ctx.get("character_id", ""))
        device_id = str(ctx.get("device_id", ""))

        # 短期记忆：追加用户提问
        if self.memory_manager:
            self.memory_manager.add_dialogue(user_id, "user", user_text)

        # 长期记忆：RAG 检索
        ltm_context = ""
        if self.memory_manager:
            try:
                ltm_context = self.memory_manager.retrieve_context(
                    query=user_text,
                    user_id=user_id,
                    limit=5,
                    character_id=character_id,
                    device_id=device_id,
                )
            except Exception as e:
                logger.error("Commander: RAG 检索异常，继续无记忆对话: %s", e)

        system_content = _build_system_prompt(ltm_context)
        stm_messages = self.memory_manager.get_short_term_messages(user_id) if self.memory_manager else []

        # 构建 messages：System + 短期历史（已含当前 user_text）
        messages: List[Dict[str, Any]] = [{"role": "system", "content": system_content}]
        messages.extend(stm_messages)

        tools, handlers = self._get_tools_and_handlers(agent_context)

        # 无工具时退化为纯闲聊
        if not tools:
            reply = await self._chat_only(messages)
            if self.memory_manager:
                self.memory_manager.add_dialogue(user_id, "assistant", reply)
                self._maybe_trigger_dream(user_id)
            return reply

        client = self._get_client()
        try:
            # Step A: 路由决策
            response = await client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=0.7,
                max_tokens=1024,
            )
        except Exception as e:
            logger.warning("Commander LLM 调用失败，回退闲聊: %s", e)
            return await self._chat_only(messages)

        msg = response.choices[0].message if response.choices else None
        if not msg:
            return await self._chat_only(messages)

        tool_calls = getattr(msg, "tool_calls", None) or []
        if not tool_calls:
            content = getattr(msg, "content", None) or ""
            if content:
                reply = content.strip()
                if self.memory_manager:
                    self.memory_manager.add_dialogue(user_id, "assistant", reply)
                    self._maybe_trigger_dream(user_id)
                return reply
            reply = await self._chat_only(messages)
            if self.memory_manager:
                self.memory_manager.add_dialogue(user_id, "assistant", reply)
                self._maybe_trigger_dream(user_id)
            return reply

        # Step B: 沙箱执行
        def _tc_to_dict(t):
            if isinstance(t, dict):
                return t
            fn = getattr(t, "function", None)
            return {
                "id": getattr(t, "id", "call_0"),
                "type": "function",
                "function": {
                    "name": getattr(fn, "name", "") if fn else "",
                    "arguments": getattr(fn, "arguments", "{}") if fn else "{}",
                },
            }
        assistant_msg = {"role": "assistant", "content": getattr(msg, "content", "") or "", "tool_calls": [_tc_to_dict(t) for t in tool_calls]}
        messages.append(assistant_msg)

        for tc in tool_calls:
            d = _tc_to_dict(tc)
            func = d["function"]["name"]
            args_str = d["function"]["arguments"]
            tc_id = d["id"]

            if func not in handlers:
                logger.warning("Commander: LLM 返回未知工具 %s，忽略", func)
                continue

            try:
                args = json.loads(args_str) if isinstance(args_str, str) else (args_str or {})
            except json.JSONDecodeError:
                args = {}

            try:
                result = handlers[func](args)
                if isinstance(result, dict):
                    result_text = result.get("text", result.get("tts", str(result)))
                else:
                    result_text = str(result)
            except Exception as e:
                logger.warning("Commander: 工具 %s 执行失败: %s", func, e)
                result_text = f"工具执行出错: {e}"

            messages.append({
                "role": "tool",
                "tool_call_id": tc_id,
                "content": result_text,
            })

        # Step C: 总结回复
        try:
            final = await client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0.7,
                max_tokens=512,
            )
            content = ""
            if final.choices:
                content = getattr(final.choices[0].message, "content", None) or ""
            reply = (content or "好的，主人。").strip()
            if self.memory_manager:
                self.memory_manager.add_dialogue(user_id, "assistant", reply)
                self._maybe_trigger_dream(user_id)
            return reply
        except Exception as e:
            logger.warning("Commander 总结回复失败: %s", e)
            fallback = "抱歉，我这边出了点小状况，稍后再试哦。"
            if messages:
                last_tool = next((m for m in reversed(messages) if m.get("role") == "tool"), None)
                if last_tool:
                    fallback = str(last_tool.get("content", ""))[:200] or fallback
            if self.memory_manager:
                self.memory_manager.add_dialogue(user_id, "assistant", fallback)
                self._maybe_trigger_dream(user_id)
            return fallback

    async def _chat_only(self, messages: List[Dict[str, Any]]) -> str:
        """无工具时的纯闲聊，使用完整 messages（含 System + 短期记忆）"""
        # 确保有 system 和至少一条 user
        if not messages or messages[0].get("role") != "system":
            msgs = [
                {"role": "system", "content": FALLBACK_SYSTEM_PROMPT_BASE},
                {"role": "user", "content": messages[-1].get("content", "") if messages else ""},
            ]
        else:
            msgs = messages
        try:
            client = self._get_client()
            r = await client.chat.completions.create(
                model=self.model,
                messages=msgs,
                temperature=0.7,
                max_tokens=512,
            )
            if r.choices:
                return (getattr(r.choices[0].message, "content", None) or "").strip()
        except Exception as e:
            logger.warning("Commander 闲聊回退失败: %s", e)
        return "主人，我在呢。有什么可以帮你的吗？"

    def _maybe_trigger_dream(self, user_id: str) -> None:
        """
        战役三：当 STM 达到阈值时，异步触发梦境提炼。
        不阻塞主流程。
        """
        if not self.memory_manager:
            return
        try:
            from core.memory.manager import CONSOLIDATION_THRESHOLD
            if self.memory_manager.get_stm_count(user_id) >= CONSOLIDATION_THRESHOLD:
                client = self._get_client()
                asyncio.create_task(
                    self.memory_manager.consolidate_memory(user_id, client, self.model)
                )
                logger.info("梦境提炼已触发: user_id=%s", user_id)
        except Exception as e:
            logger.warning("触发梦境失败: %s", e)


def _create_memory_manager() -> Optional[Any]:
    """创建记忆协调中枢（战役二）。失败时返回 None，Commander 将无记忆运行。"""
    try:
        from core.memory import get_lancedb_store, OpenAIEmbedder, MemoryManager
        from core.config import settings

        store = get_lancedb_store()
        key = settings.QWEN_API_KEY or settings.DASHSCOPE_API_KEY or settings.QWEN_AI_API_KEY
        if not key:
            key = getattr(settings, "OPENAI_API_KEY", None)
        base_url = None
        if settings.LLM_PROVIDER in ("qwen", "qwen-v2") and key:
            try:
                from core.brain.llm.dashscope_regional import get_dashscope_regional_api_base

                base_url = get_dashscope_regional_api_base()
            except ImportError:
                base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        embedder = OpenAIEmbedder(model="text-embedding-3-small", api_key=key, base_url=base_url)
        return MemoryManager(store=store, embedder=embedder)
    except Exception as e:
        logger.warning("MemoryManager 未就绪，Commander 将无记忆运行: %s", e)
        return None


def get_commander_agent(
    api_base: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> Optional[CommanderAgent]:
    """
    获取 CommanderAgent 单例（延迟初始化）。

    从 core.config 读取 LLM 配置，兼容 Qwen / 本地模型。
    战役二：自动注入 MemoryManager，实现 RAG 长短期记忆融合。
    """
    try:
        from core.config import settings
        from core.system.plugin_manager import get_plugin_manager

        pm = get_plugin_manager()
        base = api_base
        key = api_key
        m = model

        if base is None or key is None or m is None:
            if settings.LLM_PROVIDER in ("qwen", "qwen-v2"):
                if not base:
                    try:
                        from core.brain.llm.dashscope_regional import get_dashscope_regional_api_base

                        base = get_dashscope_regional_api_base()
                    except ImportError:
                        base = "https://dashscope.aliyuncs.com/compatible-mode/v1"
                key = key or settings.QWEN_API_KEY or settings.DASHSCOPE_API_KEY or settings.QWEN_AI_API_KEY
                m = m or settings.LLM_MODEL
            else:
                base = base or settings.LOCAL_LLM_URL
                if not base.startswith("http"):
                    base = f"http://{base}"
                base = base.rstrip("/") + "/v1"
                key = key or settings.LOCAL_LLM_API_KEY or "none"
                m = m or settings.LOCAL_LLM_MODEL

        if not key and "dashscope" in (base or ""):
            logger.warning("Commander: 未配置 Qwen API Key，将无法调用 LLM")
            return None

        memory_manager = _create_memory_manager()

        return CommanderAgent(
            plugin_manager=pm,
            api_base=base,
            api_key=key or "none",
            model=m or "gpt-4o-mini",
            memory_manager=memory_manager,
        )
    except Exception as e:
        logger.warning("Commander 初始化失败: %s", e)
        return None
