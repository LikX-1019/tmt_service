"""MySQL 异步会话工厂，按需创建并在进程内复用连接池。"""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_recycle=1800,
    )


@lru_cache(maxsize=1)
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def dispose_engine() -> None:
    """仅在连接池已经创建时释放资源，避免关闭阶段反向创建连接。"""
    if get_engine.cache_info().currsize:
        await get_engine().dispose()
        get_session_factory.cache_clear()
        get_engine.cache_clear()
