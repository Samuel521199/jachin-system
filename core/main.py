"""
Jachin-System Backend - 主应用入口

FastAPI 应用，整合所有 API 路由和中间件。
"""
# 尽早加载 .env，确保 JACHIN_L2_ADMIN_TOKEN 等被注入
try:
    from dotenv import load_dotenv
    from pathlib import Path as _Path
    for _p in [_Path(__file__).resolve().parent.parent, _Path.cwd()]:
        _e = _p / ".env"
        if _e.exists():
            load_dotenv(_e, encoding="utf-8")
            break
except ImportError:
    pass

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles


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


class _AccessLogFilter(logging.Filter):
    """过滤 uvicorn 访问日志：不打印高频轮询请求，减少控制台噪音。"""
    _NOISY_PATTERNS = (
        "GET /health ",
        "GET /api/v3/gpu/stats",
        "GET /api/v3/cluster/stats",
        "GET /api/v3/cluster/nodes",
        "GET /api/v3/cluster/tasks",
        "GET /api/v3/logs/recent",
        "GET /api/v3/memory/count",
        "GET /api/v3/config ",
        "GET /api/v2/devices ",
        "GET /api/v3/suggestions",
        "OPTIONS /api/v3/logs/recent",
    )

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
            for p in self._NOISY_PATTERNS:
                if p in msg:
                    return False
            return True
        except Exception:
            return True


logging.getLogger("uvicorn.access").addFilter(_AccessLogFilter())

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
    logger.warning(f"Skills API router not available (Ray/PluginManager): %s，使用 inventory 降级路由", e)

try:
    from core.api.orchestrator import router as orchestrator_router
except ImportError as e:
    orchestrator_router = None
    logger.warning(f"Orchestrator API router not available: {e}")

# cluster_router 已废弃（依赖已删除的 ray_cluster）
cluster_router = None

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

try:
    from core.api.routes.v2_auth import router as v2_auth_router
except ImportError as e:
    v2_auth_router = None
    logger.warning(f"V2 Auth API router not available: {e}")

try:
    from core.api.routes.v2_admin import router as v2_admin_router
except ImportError as e:
    v2_admin_router = None
    logger.warning(f"V2 Admin API router not available: {e}")

try:
    from core.api.routes.v2_local_admin import router as v2_local_admin_router
except ImportError as e:
    v2_local_admin_router = None
    logger.warning(f"V2 Local Admin API router not available: {e}")

try:
    from core.api.routes.v2_memory import router as v2_memory_router
except ImportError as e:
    v2_memory_router = None
    logger.warning(f"V2 Memory API router not available: {e}")

try:
    from core.api.routes.v2_coordinate import router as v2_coordinate_router
except ImportError as e:
    v2_coordinate_router = None
    logger.warning(f"V2 Coordinate API router not available: {e}")

try:
    from core.api.routes.v2_devices import router as v2_devices_router
except ImportError as e:
    v2_devices_router = None
    logger.warning(f"V2 Devices API router not available: {e}")

try:
    from core.api.routes.v2_mcp import router as v2_mcp_router
except ImportError as e:
    v2_mcp_router = None
    logger.warning(f"V2 MCP API router not available: {e}")

try:
    from core.api.routes.v2_inventory import router as v2_inventory_router
except ImportError as e:
    v2_inventory_router = None
    logger.warning(f"V2 Inventory API router not available: {e}")

try:
    from core.api.routes.v2_recycle_bin import router as v2_recycle_bin_router
except ImportError as e:
    v2_recycle_bin_router = None
    logger.warning(f"V2 RecycleBin API router not available: {e}")

try:
    from core.api.routes.v2_skills import router as v2_skills_router
except ImportError as e:
    v2_skills_router = None
    logger.warning(f"V2 Skills API router not available: {e}")

try:
    from core.api.routes.v2_events import router as v2_events_router
except ImportError as e:
    v2_events_router = None
    logger.warning(f"V2 Events SSE router not available: {e}")

# Lifespan管理 - v5.0 已废弃 Ray/Dapr/PostgreSQL，仅保留轻量初始化
@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理 - v5.0 极简模式"""
    heartbeat_task = None
    cloud_sync_task = None
    try:
        # V2: Ray/Dapr/PostgreSQL 已废弃；L2 控制面 + L3 单体
        app.state.plugin_manager = None
        app.state.skill_registry = None
        app.state.device_registry = None
        app.state.task_planner = None
        logger.info("Jachin-System V2 - L2 控制面 (Ray/Dapr 已废弃)")
        # L1-L2 创世溯源：若已配对，确保默认子账号存在并写入 pairing_code
        try:
            from core.bootstrap import ensure_default_sub_account, sync_api_keys_from_env
            ensure_default_sub_account()
            n = sync_api_keys_from_env()
            if n > 0:
                logger.info("单机模式：已从环境变量同步 %d 个 API Key 到默认子账号", n)
        except Exception as e:
            logger.warning("ensure_default_sub_account/sync_api_keys 跳过: %s", e)
        # L2 向量梦境引擎：初始化 LanceDB (~/.jachin/lancedb_data)
        try:
            from core.db.l2_memory_lancedb import init_l2_lancedb
            if init_l2_lancedb():
                logger.info("L2 LanceDB 向量梦境引擎已初始化")
            else:
                logger.warning("L2 LanceDB 初始化跳过（Embedder 或 lancedb 不可用）")
        except Exception as e:
            logger.warning("init_l2_lancedb 跳过: %s", e)
        # L1-L2 策略同步心跳：启动后台守护进程
        try:
            from core.sync_daemon import start_l1_heartbeat_background
            heartbeat_task = start_l1_heartbeat_background()
        except Exception as e:
            logger.warning("L1 心跳守护进程启动跳过: %s", e)
        # L1-L2 云边同步：神谕 manifest 拉取 + 技能/MCP 空投
        try:
            from core.sync_daemon import start_cloud_sync_background
            cloud_sync_task = await start_cloud_sync_background(interval_seconds=60)
        except Exception as e:
            logger.warning("云边同步守护进程启动跳过: %s", e)
        # 本地数字仓库：确保目录存在
        try:
            from core.inventory_scanner import ensure_inventory_dirs
            ensure_inventory_dirs()
        except Exception as e:
            logger.warning("Inventory 目录初始化跳过: %s", e)
        # MCP 客户端代理：读取 ~/.jachin/mcp_servers.json，并发拉起所有 Server
        try:
            from core.mcp_client import get_mcp_manager
            mcp_manager = get_mcp_manager()
            await mcp_manager.start()
            logger.info("MCP 管理器已启动 servers=%d tools=%d", mcp_manager.server_count, mcp_manager.tool_count)
        except Exception as e:
            logger.warning("MCP 管理器启动跳过: %s", e)
        # 本地仓库扫描：侧载 MCP 与 Wasm 技能
        try:
            from core.inventory_scanner import scan_local_mcps, scan_local_skills
            mcp_injected = await scan_local_mcps()
            skills_found = await scan_local_skills()
            if mcp_injected or skills_found:
                logger.info("Inventory 扫描完成 mcps=%d skills=%d", mcp_injected, skills_found)
        except Exception as e:
            logger.warning("Inventory 扫描跳过: %s", e)
        # 断网自治：从本地 role_permissions 预加载 RBAC 策略（L1 同步前或断网时作为真理来源）
        try:
            from core.policy_enforcer import load_from_local_db
            if load_from_local_db():
                logger.info("PolicyEnforcer 已从本地策略加载（断网自治就绪）")
        except Exception as e:
            logger.debug("PolicyEnforcer 本地预加载跳过: %s", e)
    except Exception as e:
        logger.error(f"Failed to initialize: {e}", exc_info=True)

    yield

    # MCP 优雅关闭
    try:
        from core.mcp_client import get_mcp_manager
        await get_mcp_manager().stop()
    except Exception as e:
        logger.warning("MCP 管理器关闭异常: %s", e)
    if heartbeat_task and not heartbeat_task.done():
        heartbeat_task.cancel()
        try:
            import asyncio
            await asyncio.wait_for(asyncio.shield(heartbeat_task), timeout=2.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
    if cloud_sync_task and not cloud_sync_task.done():
        cloud_sync_task.cancel()
        try:
            import asyncio
            await asyncio.wait_for(asyncio.shield(cloud_sync_task), timeout=2.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
    logger.info("Shutting down Jachin Nexus v0.8.32 (Singularity OS)...")


# 创建 FastAPI 应用
app = FastAPI(
    title="Jachin-System Backend",
    version="0.8.32",
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
# L1 订阅欠费拦截：挂起 L3 请求，返回 402
try:
    from core.middleware.l1_subscription import L1SubscriptionMiddleware
    app.add_middleware(L1SubscriptionMiddleware)
except ImportError as e:
    logger.warning("L1SubscriptionMiddleware 未加载: %s", e)

# L2 本地管理控制台静态资源
_static_admin = Path(__file__).resolve().parent.parent / "static" / "admin"
if _static_admin.exists():
    app.mount("/admin", StaticFiles(directory=str(_static_admin), html=True), name="admin")
    logger.info("Admin console: /admin")

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
    # device_router 依赖 Dapr，已废弃；/api/v2/devices 由 v2_devices_router 提供
    if create_subscription_router:
        app.include_router(create_subscription_router())
if skills_router:
    app.include_router(skills_router)
else:
    from core.api.skills_fallback import router as skills_fallback_router
    app.include_router(skills_fallback_router)
if orchestrator_router:
    app.include_router(orchestrator_router)
if monitoring_router:
    app.include_router(monitoring_router)
if config_router:
    app.include_router(config_router)
if console_router:
    app.include_router(console_router)
if v2_auth_router:
    app.include_router(v2_auth_router)
if v2_admin_router:
    app.include_router(v2_admin_router)
if v2_local_admin_router:
    app.include_router(v2_local_admin_router)
if v2_memory_router:
    app.include_router(v2_memory_router)
if v2_coordinate_router:
    app.include_router(v2_coordinate_router)
if v2_devices_router:
    app.include_router(v2_devices_router)
if v2_mcp_router:
    app.include_router(v2_mcp_router)
if v2_inventory_router:
    app.include_router(v2_inventory_router)
if v2_recycle_bin_router:
    app.include_router(v2_recycle_bin_router)
if v2_skills_router:
    app.include_router(v2_skills_router)
if v2_events_router:
    app.include_router(v2_events_router)
logger.info("Routes: /api, /api/v2/auth/sync, /api/v2/auth/check, /api/v2/keys, /api/v2/devices, /api/v2/memory/*, /api/v2/mcp/*, /api/v2/inventory/*, /api/v2/events/*, /api/v2/coordinate/*, /api/v2/admin/*, /api/v3/*")

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

# Dapr 端点已移除（V2 架构：全面弃用 Dapr）

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

# L2 审批面板（神经接驳、节点分配）：/gateway
# static/admin（武库大盘）已挂载于 /admin，此处挂载到 /gateway 避免覆盖
admin_ui_dir = os.path.join(os.path.dirname(__file__), "admin_ui")
if os.path.exists(admin_ui_dir):
    from fastapi.staticfiles import StaticFiles
    app.mount("/gateway", StaticFiles(directory=admin_ui_dir, html=True), name="admin_ui")
    logger.info("L2 审批面板: /gateway/")

# 推理策略 API（运行模式切换：节能/默认/高性能/上帝模式）
# V2: 使用进程内默认值（Dapr 已废弃）
INFERENCE_STRATEGY_KEY = "inference/strategy"
INFERENCE_STRATEGY_DEFAULT = {"mode": "default"}
_state_store = None

@app.get("/api/v3/inference/strategy")
async def get_inference_strategy():
    """获取当前推理策略（运行模式）"""
    if _state_store is None:
        return INFERENCE_STRATEGY_DEFAULT
    val = await _state_store.get(INFERENCE_STRATEGY_KEY, INFERENCE_STRATEGY_DEFAULT)
    return val if isinstance(val, dict) and "mode" in val else INFERENCE_STRATEGY_DEFAULT

@app.post("/api/v3/inference/strategy")
async def set_inference_strategy(body: dict):
    """
    设置推理策略。body: {"mode": "power"|"default"|"perf"|"god"}
    V2: 进程内生效（Dapr 已废弃）
    """
    mode = (body or {}).get("mode", "default")
    allowed = ("power", "default", "perf", "god")
    if mode not in allowed:
        return {"ok": False, "error": f"mode must be one of {allowed}"}
    payload = {"mode": mode}
    if _state_store is not None:
        ok = await _state_store.save(INFERENCE_STRATEGY_KEY, payload)
        if not ok:
            logger.warning("Failed to persist inference strategy")
    logger.info("Inference strategy set to: %s", mode)
    return {"ok": True, "mode": mode}

if __name__ == "__main__":
    from core.single_instance import acquire_single_instance_lock
    acquire_single_instance_lock("l2", kill_previous=True)  # 同设备仅允许一个 L2，启动时杀死旧实例

    import uvicorn
    from core.config import settings

    uvicorn.run(
        "core.main:app",
        host=settings.SERVER_HOST,
        port=settings.effective_port,
        reload=False,
    )
