"""异步 SQLAlchemy engine + session 工厂。

单例生命周期：由 HistoryService 持有 engine 与 sessionmaker；
lifespan 关闭时 dispose 释放连接。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from deepquery.config.settings import Settings


def make_engine(settings: Settings) -> AsyncEngine:
    # SQLite 使用 NullPool 以外的默认池即可；连接数极低时不值得调优。
    # future=True 已是 2.x 默认，显式写出便于日后切 driver 时核对。
    return create_async_engine(
        settings.database_url,
        echo=settings.database_echo,
        future=True,
    )


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
