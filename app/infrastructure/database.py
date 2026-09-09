from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings


class Database:
    def __init__(self, settings: Settings):
        self.engine = create_async_engine(
            settings.database_url.get_secret_value(),
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_pre_ping=True,
            hide_parameters=True,
            pool_timeout=10,
            pool_recycle=1800,
            connect_args={
                "timeout": 5,
                "command_timeout": 30,
                "server_settings": {
                    "application_name": settings.app_name,
                    "statement_timeout": "15000",
                    "idle_in_transaction_session_timeout": "30000",
                },
            },
        )
        self.sessions = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

    async def ping(self) -> None:
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def close(self) -> None:
        await self.engine.dispose()
