"""
简化的主入口 - 用于 MVP 验证

这是服务器入口，展示核心逻辑。
"""

from fastapi import FastAPI
from pydantic import BaseModel
import logging

# 导入简化的 Provider
try:
    from core.brain.llm.provider import QwenProvider
except ImportError:
    QwenProvider = None
    logging.warning("QwenProvider not available")

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# 创建 FastAPI 应用
app = FastAPI(title="Jachin-System MVP", version="1.0.0")

# 初始化 LLM Provider
llm = None
if QwenProvider:
    try:
        llm = QwenProvider()
        logger.info("QwenProvider initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize QwenProvider: {e}")
        logger.error("Please check your API key configuration")


# 定义请求格式
class ChatRequest(BaseModel):
    """聊天请求模型"""
    message: str


class ChatResponse(BaseModel):
    """聊天响应模型"""
    reply: str


@app.post("/api/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    """
    聊天接口

    这是核心逻辑：
    1. 这里未来会加入：检索 LanceDB / 生物学记忆上下文
    2. 这里未来会加入：查询 Dapr 状态
    3. 调用大模型
    """
    if not llm:
        return ChatResponse(reply="Error: LLM Provider is not available. Please check configuration.")

    # 调用大模型
    response = await llm.chat(request.message)

    return ChatResponse(reply=response)


@app.get("/")
async def root():
    """根路径"""
    return {
        "name": "Jachin-System",
        "version": "1.0.0",
        "status": "running",
        "llm_available": llm is not None
    }


@app.get("/health")
async def health():
    """健康检查"""
    return {
        "status": "healthy",
        "llm_available": llm is not None
    }


if __name__ == "__main__":
    import uvicorn
    from core.config import settings

    uvicorn.run(
        "core.main_simple:app",
        host=settings.SERVER_HOST,
        port=settings.effective_port,
        reload=False,
    )
