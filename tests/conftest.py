"""
pytest配置和fixtures
Pytest Configuration and Fixtures
"""

import pytest
import asyncio
import ray
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base

# 测试数据库URL（使用内存数据库或测试数据库）
TEST_DATABASE_URL = "postgresql+asyncpg://jachin:secure_password@localhost:5432/jachin_brain_test"

# 创建测试引擎
test_engine = create_async_engine(
    TEST_DATABASE_URL,
    echo=False,
    future=True,
)

# 测试会话工厂
TestSessionLocal = async_sessionmaker(
    test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# 测试基类
TestBase = declarative_base()


@pytest.fixture(scope="session")
def event_loop():
    """创建事件循环"""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="function")
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """创建测试数据库会话"""
    async with TestSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


@pytest.fixture(scope="function")
async def init_test_db():
    """初始化测试数据库。V2: core.memory.schema 已废弃，此 fixture 跳过。"""
    try:
        from core.memory.schema.database import Base
    except ImportError:
        pytest.skip("core.memory.schema 已废弃 (V2)")
        return
    
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    yield
    
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    
    await test_engine.dispose()


@pytest.fixture(scope="module", autouse=False)
def ray_init():
    """初始化 Ray（模块级别，所有测试共享）
    
    注意：使用 autouse=False，只有明确请求此 fixture 的测试才会初始化 Ray
    这样可以避免不需要 Ray 的测试也初始化 Ray
    """
    if not ray.is_initialized():
        ray.init(ignore_reinit_error=True, num_cpus=2)
    yield
    # 注意：不在这里 shutdown，让 pytest 管理 Ray 生命周期
