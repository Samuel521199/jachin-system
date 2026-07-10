"""
pytest配置和fixtures
Pytest Configuration and Fixtures
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, AsyncGenerator

import pytest

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# 测试数据库URL（使用内存数据库或测试数据库）
TEST_DATABASE_URL = "postgresql+asyncpg://jachin:secure_password@localhost:5432/jachin_brain_test"

_test_engine: Any = None
_test_session_factory: Any = None


def _ensure_test_db() -> tuple[Any, Any]:
    """按需初始化 SQLAlchemy 测试库（未安装 sqlalchemy 时由 fixture 跳过）。"""
    global _test_engine, _test_session_factory
    if _test_session_factory is not None:
        return _test_engine, _test_session_factory

    pytest.importorskip("sqlalchemy")
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    _test_engine = create_async_engine(
        TEST_DATABASE_URL,
        echo=False,
        future=True,
    )
    _test_session_factory = async_sessionmaker(
        _test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    return _test_engine, _test_session_factory


@pytest.fixture(scope="session")
def event_loop():
    """创建事件循环"""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="function")
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """创建测试数据库会话"""
    _, session_factory = _ensure_test_db()
    async with session_factory() as session:
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

    test_engine, _ = _ensure_test_db()

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await test_engine.dispose()


@pytest.fixture(scope="module", autouse=False)
def ray_init():
    """初始化 Ray（模块级别，所有测试共享）

    注意：使用 autouse=False，只有明确请求此 fixture 的测试才会初始化 Ray。
    ray 为可选依赖，未安装时跳过依赖本 fixture 的测试。
    """
    ray = pytest.importorskip("ray")
    if not ray.is_initialized():
        ray.init(ignore_reinit_error=True, num_cpus=2)
    yield
    # 注意：不在这里 shutdown，让 pytest 管理 Ray 生命周期
