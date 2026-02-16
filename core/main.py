"""
Jachin-System Backend - 主应用入口

FastAPI 应用，整合所有 API 路由和中间件。
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse


class UTF8JSONResponse(JSONResponse):
    """JSON 响应强制 UTF-8，避免 PowerShell 等客户端按 Latin-1 解码导致中文乱码"""
    media_type = "application/json; charset=utf-8"
from contextlib import asynccontextmanager
import os
import logging
import sys
import yaml
from pathlib import Path

# 设置日志编码为 UTF-8，避免 Windows 控制台乱码
if sys.platform == "win32":
    try:
        import codecs
        sys.stdout = codecs.getwriter("utf-8")(sys.stdout.buffer, "strict")
        sys.stderr = codecs.getwriter("utf-8")(sys.stderr.buffer, "strict")
    except Exception:
        pass

# 统一日志配置（控制台 + 文件）
from core.logger import setup_logging, get_logger
setup_logging()
logger = get_logger(__name__)

# 绑定控制台日志缓冲，供 /api/v3/logs/recent 思维流使用
try:
    from core.api.console import ConsoleLogHandler
    _console_handler = ConsoleLogHandler()
    _console_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logging.getLogger().addHandler(_console_handler)
except Exception:
    pass

# 导入 API 路由
try:
    from core.api.chat import router as chat_router, simple_router as chat_simple_router
except ImportError as e:
    chat_router = None
    chat_simple_router = None
    logger.error(f"Failed to import Chat API router: {e}", exc_info=True)

try:
    from core.api.chat_v2 import router as chat_v2_router
except ImportError as e:
    chat_v2_router = None
    logger.warning(f"Chat API V2 router not available: {e}")

try:
    from core.api.voice import router as voice_router
except ImportError as e:
    voice_router = None
    logger.warning(f"Voice API router not available: {e}")

try:
    from core.api.tts_models import router as tts_models_router
except ImportError as e:
    tts_models_router = None
    logger.warning(f"TTS models router not available: {e}")

try:
    from core.api.routes.handshake import router as handshake_router, device_router, create_subscription_router
except ImportError as e:
    handshake_router = None
    device_router = None
    create_subscription_router = None
    logger.warning(f"Handshake API router not available: {e}")

try:
    from core.api.skills import router as skills_router
except ImportError as e:
    skills_router = None
    logger.warning(f"Skills API router not available: {e}")

try:
    from core.api.orchestrator import router as orchestrator_router
except ImportError as e:
    orchestrator_router = None
    logger.warning(f"Orchestrator API router not available: {e}")

try:
    from core.api.cluster import router as cluster_router
except ImportError as e:
    cluster_router = None
    logger.warning(f"Cluster API router not available: {e}")

try:
    from core.api.monitoring import router as monitoring_router
except ImportError as e:
    monitoring_router = None
    logger.warning(f"Monitoring API router not available: {e}")

try:
    from core.api.config import router as config_router
except ImportError as e:
    config_router = None
    logger.warning(f"Config API router not available: {e}")

try:
    from core.api.console import router as console_router
except ImportError as e:
    console_router = None
    logger.warning(f"Console API router not available: {e}")

# Lifespan管理
@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    try:
        from core.memory.schema import init_database
        await init_database()
        logger.info("DB ready")
        from core.brain.ray_cluster import RayClusterManager
        ray_manager = RayClusterManager()
        await ray_manager.initialize()
        app.state.ray_manager = ray_manager
        logger.info("Ray ready")
        from core.system.plugin_manager import get_plugin_manager
        plugin_mgr = get_plugin_manager()
        n_skills = plugin_mgr.load_skills()
        app.state.plugin_manager = plugin_mgr
        app.state.skill_registry = plugin_mgr  # 兼容旧 API
        logger.info("Skills: %d loaded", n_skills)
        from core.registry.registry import DeviceRegistry
        device_registry = DeviceRegistry()
        app.state.device_registry = device_registry
        from core.brain.planner import TaskPlanner, IntentParser, ResourceAllocator
        intent_parser = IntentParser()
        resource_allocator = ResourceAllocator(ray_manager)
        task_planner = TaskPlanner(
            intent_parser=intent_parser,
            resource_allocator=resource_allocator,
            plugin_manager=plugin_mgr,
            device_registry=device_registry,
        )
        app.state.task_planner = task_planner
        logger.info("Jachin-System v3.2 started")
        
    except Exception as e:
        logger.error(f"Failed to initialize Jachin-System v3.2: {e}", exc_info=True)
        # 继续启动，但某些功能可能不可用
    
    yield
    
    # 关闭时执行
    logger.info("Shutting down Jachin-System v3.2...")
    
    try:
        # 关闭Ray集群
        if hasattr(app.state, "ray_manager") and app.state.ray_manager:
            await app.state.ray_manager.shutdown()
        
        # 关闭数据库连接
        from core.memory.schema import close_database
        await close_database()
        
        logger.info("Jachin-System v3.2 shutdown complete")
    except Exception as e:
        logger.error(f"Error during shutdown: {e}", exc_info=True)


# 创建 FastAPI 应用
app = FastAPI(
    title="Jachin-System Backend",
    version="3.2.0",
    description="Jachin-System AI Agent Backend API v3.2",
    lifespan=lifespan,
    # 确保 JSON 响应使用 UTF-8 编码
    default_response_class=UTF8JSONResponse
)

# 配置 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 开发环境允许所有来源
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
if chat_router:
    app.include_router(chat_router)
if chat_simple_router:
    app.include_router(chat_simple_router)
if chat_v2_router:
    app.include_router(chat_v2_router)
if voice_router:
    app.include_router(voice_router)
if tts_models_router:
    app.include_router(tts_models_router)
if handshake_router:
    app.include_router(handshake_router)
    if device_router:
        app.include_router(device_router)
    if create_subscription_router:
        app.include_router(create_subscription_router())
if skills_router:
    app.include_router(skills_router)
if orchestrator_router:
    app.include_router(orchestrator_router)
if cluster_router:
    app.include_router(cluster_router)
if monitoring_router:
    app.include_router(monitoring_router)
if config_router:
    app.include_router(config_router)
if console_router:
    app.include_router(console_router)
logger.info("Routes: /api, /api/v1/chat, /api/v2/chat, /api/v2/voice, /api/v3/skills, /api/v3/orchestrator, /api/v3/config, /api/v3/logs, /api/v3/suggestions, /api/v3/calendar, /api/v3/memory, /api/v3/models")

# 健康检查端点
@app.get("/health")
async def health_check():
    """健康检查端点"""
    return {
        "status": "healthy",
        "service": "jachin-brain",
        "version": "1.0.0"
    }

# 测试端点 - 验证路由注册
@app.get("/test")
async def test():
    """测试端点"""
    return {
        "message": "Backend is working",
        "routers": {
            "chat_router": chat_router is not None,
            "chat_simple_router": chat_simple_router is not None,
            "chat_v2_router": chat_v2_router is not None
        }
    }

# 根路径
@app.get("/")
async def root():
    """根路径"""
    return {
        "message": "Jachin-System Backend API",
        "version": "1.0.0",
        "docs": "/docs",
        "endpoints": {
            "health": "/health",
            "chat": "/api/chat",
            "chat_v1": "/api/v1/chat/",
            "chat_v2": "/api/v2/chat",
            "voice": "/api/v2/voice",
            "voice_test": "/voice-test"
        }
    }

# 列出所有路由（用于调试）
@app.get("/routes")
async def list_routes():
    """列出所有注册的路由"""
    routes = []
    for route in app.routes:
        if hasattr(route, "path") and hasattr(route, "methods"):
            routes.append({
                "path": route.path,
                "methods": list(route.methods)
            })
    return {"routes": routes}

# Dapr 标准端点（可选，用于消除 404 警告）
@app.get("/dapr/config")
async def dapr_config():
    """Dapr 配置端点（可选）"""
    # 返回空配置，表示使用默认配置
    return {}

@app.get("/dapr/subscribe")
async def dapr_subscribe():
    """
    Dapr 订阅端点
    
    返回订阅的主题列表，告诉 Dapr 哪些主题需要推送消息到此应用。
    """
    subscriptions = [
        {
            "pubsubname": "pubsub",
            "topic": "system/announce",
            "route": "/dapr/subscribe/system/announce"
        },
        {
            "pubsubname": "pubsub",
            "topic": "system/heartbeat",
            "route": "/dapr/subscribe/system/heartbeat"
        },
        {
            "pubsubname": "pubsub",
            "topic": "system/unregister",
            "route": "/dapr/subscribe/system/unregister"
        }
    ]
    logger.debug("Dapr subscriptions: %s topics", len(subscriptions))
    return subscriptions

# 静态文件服务（用于 web 前端）
web_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web")
if os.path.exists(web_dir):
    from fastapi.staticfiles import StaticFiles
    app.mount("/static", StaticFiles(directory=web_dir), name="static")
    
    @app.get("/chat")
    async def chat_page():
        """聊天页面"""
        index_path = os.path.join(web_dir, "index.html")
        if os.path.exists(index_path):
            return FileResponse(index_path)
        return {"error": "Chat page not found"}
    
    @app.get("/voice-test")
    async def voice_test_page():
        """语音测试页面"""
        voice_test_path = os.path.join(web_dir, "voice-test.html")
        if os.path.exists(voice_test_path):
            return FileResponse(voice_test_path)
        return {"error": "Voice test page not found"}

# Tier 2 Hive Dashboard（ROG 式管理后台）
web_ui_dir = os.path.join(os.path.dirname(__file__), "web_ui")
if os.path.exists(web_ui_dir):
    from fastapi.staticfiles import StaticFiles
    app.mount("/hive", StaticFiles(directory=web_ui_dir, html=True), name="hive")
    logger.info("Hive Dashboard: /hive/")

# 推理策略 API（运行模式切换：节能/默认/高性能/上帝模式）
# 使用 Dapr 状态存储持久化；Dapr 不可用时 StateStore 自动降级为进程内内存
INFERENCE_STRATEGY_KEY = "inference/strategy"
INFERENCE_STRATEGY_DEFAULT = {"mode": "default"}

try:
    from core.dapr.state_store import state_store as _state_store
except Exception:
    _state_store = None

@app.get("/api/v3/inference/strategy")
async def get_inference_strategy():
    """获取当前推理策略（运行模式），从 Dapr 状态读取"""
    if _state_store is None:
        return INFERENCE_STRATEGY_DEFAULT
    val = await _state_store.get(INFERENCE_STRATEGY_KEY, INFERENCE_STRATEGY_DEFAULT)
    return val if isinstance(val, dict) and "mode" in val else INFERENCE_STRATEGY_DEFAULT

@app.post("/api/v3/inference/strategy")
async def set_inference_strategy(body: dict):
    """
    设置推理策略。body: {"mode": "power"|"default"|"perf"|"god"}
    写入 Dapr 状态存储，重启后仍生效；多实例间共享（取决于 Dapr 后端）。
    """
    mode = (body or {}).get("mode", "default")
    allowed = ("power", "default", "perf", "god")
    if mode not in allowed:
        return {"ok": False, "error": f"mode must be one of {allowed}"}
    payload = {"mode": mode}
    if _state_store is not None:
        ok = await _state_store.save(INFERENCE_STRATEGY_KEY, payload)
        if not ok:
            logger.warning("Failed to persist inference strategy to Dapr state")
    logger.info("Inference strategy set to: %s", mode)
    return {"ok": True, "mode": mode}

if __name__ == "__main__":
    import uvicorn
    from core.config import settings

    uvicorn.run(
        "core.main:app",
        host=settings.SERVER_HOST,
        port=settings.effective_port,
        reload=False,
    )
