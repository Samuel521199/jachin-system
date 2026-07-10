"""
简化的 LLM Provider 实现 - 用于 MVP 验证

这是核心接口，未来切换本地模型时只需修改实现类。
"""

from abc import ABC, abstractmethod
import logging

try:
    import dashscope
    from dashscope.api_entities.dashscope_response import Role
    DASHSCOPE_AVAILABLE = True
except ImportError:
    DASHSCOPE_AVAILABLE = False
    logging.warning("dashscope not installed. QwenProvider will not work.")

logger = logging.getLogger(__name__)


class LLMProvider(ABC):
    """LLM Provider 抽象基类（确保以后换 DeepSeek/Llama 不用改业务逻辑）"""

    @abstractmethod
    async def chat(self, prompt: str) -> str:
        """
        聊天接口

        Args:
            prompt: 用户输入的提示文本

        Returns:
            模型返回的文本响应
        """
        pass


class QwenProvider(LLMProvider):
    """阿里云 Qwen 实现"""

    def __init__(self, api_key: str = None, model: str = "qwen-turbo"):
        """
        初始化 Qwen Provider

        Args:
            api_key: API 密钥（如果为 None，从环境变量读取）
            model: 模型名称（qwen-turbo, qwen-plus, qwen-max）
        """
        if not DASHSCOPE_AVAILABLE:
            raise ImportError(
                "dashscope package is required. Install it with: pip install dashscope"
            )

        from core.config import settings
        self.api_key = (
            api_key
            or settings.QWEN_API_KEY
            or settings.DASHSCOPE_API_KEY
            or settings.QWEN_AI_API_KEY
        )

        if not self.api_key:
            raise ValueError(
                "Qwen API Key is required. "
                "Set one of these environment variables: QWEN_API_KEY, DASHSCOPE_API_KEY, or QWEN_AI_API_KEY"
            )

        self.model = model
        dashscope.api_key = self.api_key
        logger.info(f"Initialized QwenProvider with model: {model}")

    async def chat(self, prompt: str) -> str:
        """
        调用 Qwen 模型进行对话

        Args:
            prompt: 用户输入的提示文本

        Returns:
            模型返回的文本响应
        """
        try:
            # 开发阶段用 Turbo 省钱，生产用 Max
            # 模型映射
            model_map = {
                "qwen-turbo": dashscope.Generation.Models.qwen_turbo,
                "qwen-plus": dashscope.Generation.Models.qwen_plus,
                "qwen-max": dashscope.Generation.Models.qwen_max,
            }

            dashscope_model = model_map.get(self.model, dashscope.Generation.Models.qwen_turbo)

            response = dashscope.Generation.call(
                model=dashscope_model,
                messages=[{'role': Role.USER, 'content': prompt}],
                result_format='message'
            )

            if response.status_code == 200:
                return response.output.choices[0].message.content
            else:
                error_msg = f"Error: {response.message}"
                logger.error(error_msg)
                return error_msg

        except Exception as e:
            error_msg = f"Error calling Qwen API: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return error_msg
