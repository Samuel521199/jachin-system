"""
Configuration management - 配置管理

使用 pydantic-settings BaseSettings 管理环境变量，支持 .env 文件。
所有配置应通过 settings 单例访问，禁止在业务代码中直接使用 os.getenv。
"""

from typing import Optional
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    应用配置 - 统一管理环境变量与 .env

    环境变量优先级：显式传入 > 环境变量 > .env 文件 > 默认值
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # Server
    SERVER_HOST: str = "0.0.0.0"
    SERVER_PORT: int = 18888
    APP_PORT: Optional[int] = None  # 若设置则覆盖 SERVER_PORT（uvicorn 用）
    DEBUG: bool = True

    # Database
    DATABASE_URL: str = "postgresql://jachin:secure_password@localhost:5432/jachin_brain"

    # Vector Database (Qdrant)
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_GRPC_URL: str = "http://localhost:6334"

    # LanceDB（RAG 战役一、Edge L1 本地向量库）
    LANCEDB_PATH: str = "data/lancedb"

    # Redis
    REDIS_URL: str = "redis://localhost:6379"

    # MQTT
    MQTT_BROKER: str = "mqtt://localhost:1883"

    # LLM Configuration
    LLM_PROVIDER: str = "qwen-v2"
    QWEN_API_KEY: Optional[str] = None
    DASHSCOPE_API_KEY: Optional[str] = None
    QWEN_AI_API_KEY: Optional[str] = None
    LLM_MODEL: str = "qwen-max"
    QWEN_REGION: str = "cn-beijing"

    # Local LLM (小脑 / Edge - Ollama 默认)
    LOCAL_LLM_URL: str = "http://localhost:11434"
    LOCAL_LLM_MODEL: str = "qwen2.5:0.5b"
    LOCAL_LLM_API_KEY: Optional[str] = None

    # Layer 1 Cloud (Jachin Nexus)
    CLOUD_MARKET_URL: str = "https://market.jachin.io"  # 技能市场 API 基地址
    NEXUS_BASE_URL: str = "http://localhost:3000"     # Nexus 本地开发地址
    CLOUD_AUTH_URL: str = "https://auth.jachin.io"      # OAuth2 认证端点
    CLOUD_CLIENT_ID: Optional[str] = None               # OAuth2 client_id
    CLOUD_CLIENT_SECRET: Optional[str] = None           # OAuth2 client_secret
    HOME_DOMAIN_ID: Optional[str] = None                # 当前家庭域 ID，sync_licenses 时使用

    # Security
    SECRET_KEY: str = "your-secret-key-change-in-production"
    JWT_SECRET: str = "your-jwt-secret-change-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRATION_HOURS: int = 24

    # Device
    DEVICE_ID: Optional[str] = None

    # Whisper / Voice
    WHISPER_MODEL_PATH: Optional[str] = None
    ALIYUN_APP_KEY: Optional[str] = None
    XDG_CACHE_HOME: Optional[str] = None

    # TTS Kokoro 模型目录（供 Tier 3 下载 kokoro-v0_19.onnx, voices.json）
    TTS_MODELS_DIR: Optional[str] = None  # 默认 ./data/tts

    # Dapr
    DAPR_HTTP_PORT: str = "3500"
    DAPR_GRPC_PORT: str = "50001"

    # Project
    JACHIN_PROJECT_ROOT: Optional[str] = None
    JACHIN_DATA_DIR: Optional[str] = None  # 技能数据目录，默认 ~/.jachin

    # Sentinel
    SENTINEL_TEST_MODE: str = "0"

    # Ray
    RAY_MODE: str = "single"
    RAY_HEAD_HOST: str = "localhost"
    RAY_HEAD_PORT: int = 10001
    RAY_DASHBOARD_PORT: int = 8265
    RAY_DISABLE_USAGE_STATS: bool = True

    # Skills
    SKILLS_REPO_PATH: str = "./skills_repo"
    RESOURCES_REPO_PATH: str = "./resources_repo"  # resource_mount 型 Persona/Memory 只读挂载点
    SKILL_RUNTIME: str = "docker"
    SKILL_AUTO_ENABLE: bool = True
    SKILL_MAX_CONCURRENT: int = 10
    SKILL_DEFAULT_TIMEOUT: int = 300
    BRAIN_BASE_URL: str = "http://localhost:18888"  # L2 地址，Distributed 模式下拉取技能用

    # Topology (ARCHITECTURE_DESIGN_SPEC §3.3)
    NODE_MODE: str = "standalone"  # standalone=Super Node | distributed=分布式集群
    IS_BRAIN_LOCAL: bool = True  # L2 Brain 是否与本机同机（Super Node 时为 True，可零拷贝）

    # Cluster
    CLUSTER_MODE: str = "single"
    ENVIRONMENT: Optional[str] = None
    DISCOVERY_METHOD: str = "mdns"
    CLUSTER_NODE_ID: Optional[str] = None
    CLUSTER_NODE_NAME: Optional[str] = None
    CLUSTER_NODE_ROLE: str = "head"

    # Config paths
    CLUSTER_CONFIG_PATH: str = "./config/cluster.yaml"
    RAY_CONFIG_PATH: str = "./config/ray_config.yaml"
    SKILLS_CONFIG_PATH: str = "./config/skills_config.yaml"

    # Logging Configuration
    LOG_LEVEL: str = "INFO"
    LOG_FILE_PATH: str = "logs/jachin.log"
    LOG_ROTATION: str = "00:00"  # 每日轮转
    LOG_RETENTION: str = "10 days"  # 保留10天

    @model_validator(mode="after")
    def _resolve_qwen_api_key(self) -> "Settings":
        """统一 Qwen API Key：QWEN_AI_API_KEY > DASHSCOPE_API_KEY > QWEN_API_KEY"""
        resolved = (
            self.QWEN_AI_API_KEY or self.DASHSCOPE_API_KEY or self.QWEN_API_KEY
        )
        if resolved:
            object.__setattr__(self, "QWEN_API_KEY", resolved)
            if not self.QWEN_AI_API_KEY:
                object.__setattr__(self, "QWEN_AI_API_KEY", resolved)
        return self

    @property
    def effective_port(self) -> int:
        """uvicorn 使用的端口：APP_PORT > SERVER_PORT"""
        if self.APP_PORT is not None:
            return self.APP_PORT
        return self.SERVER_PORT


# 全局配置实例
settings = Settings()


def get_effective_qwen_api_key() -> Optional[str]:
    """
    获取有效的 Qwen/DashScope API Key（瀑布流降级读取）。

    优先级：env > nexus_config.json llm_keys.dashscope > .qwen_api_key > settings
    详见 docs/whitepaper/PLUGGABLE_COGNITIVE_ENGINES.md
    """
    try:
        from core.brain.llm.credential_loader import get_dashscope_key
        return get_dashscope_key(required=False)
    except (ImportError, ValueError):
        pass
    from core.config.api_key_override import get_qwen_api_key_override
    override = get_qwen_api_key_override()
    if override:
        return override
    return (
        settings.QWEN_AI_API_KEY
        or settings.DASHSCOPE_API_KEY
        or settings.QWEN_API_KEY
    )
