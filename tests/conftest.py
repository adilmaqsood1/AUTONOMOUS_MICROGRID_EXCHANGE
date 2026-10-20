import os

os.environ.setdefault("MGX_JWT_SECRET", "test-secret-not-for-production")
os.environ.setdefault("MGX_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("MGX_REDIS_URL", "")
os.environ.setdefault("MGX_RATE_LIMIT_PER_MINUTE", "0")

import pytest_asyncio  # noqa: E402

from app.db import session as dbsession  # noqa: E402
from app.db.base import Base  # noqa: E402
import app.models  # noqa: E402,F401
from app.db.seed import seed_demo  # noqa: E402


@pytest_asyncio.fixture
async def engine():
    eng = dbsession.init_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)  # tests only; production uses Alembic
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine):
    async with dbsession.get_sessionmaker()() as s:
        yield s


@pytest_asyncio.fixture
async def demo(session):
    info = await seed_demo(session, "test-password-123", history_hours=48)
    await session.commit()
    return info
