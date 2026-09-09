import asyncio

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import get_settings
from app.models import Base

target_metadata = Base.metadata


def run_migrations(connection=None):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        url=get_settings().database_url.get_secret_value() if connection is None else None,
        literal_binds=connection is None,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def online():
    engine = async_engine_from_config(
        {"sqlalchemy.url": get_settings().database_url.get_secret_value()},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with engine.connect() as connection:
        await connection.run_sync(run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations()
else:
    asyncio.run(online())
