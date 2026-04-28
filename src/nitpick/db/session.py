from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from nitpick.config import settings

# psycopg sync URL → asyncpg URL for async operations
_async_url = settings.database_url.replace(
    "postgresql+psycopg", "postgresql+asyncpg"
).replace(
    "postgresql://", "postgresql+asyncpg://"
)

engine = create_async_engine(_async_url, echo=(settings.log_level == "debug"))

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession]:
    async with async_session() as session:
        yield session
