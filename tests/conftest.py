"""Fixtures: BD SQLite en memoria + cliente HTTP + seed mínimo."""
import asyncio
import os

# Las tablas canónicas usan tipos propios de PostgreSQL (esquema admin, ARRAY, CITEXT): para probarlas de verdad
# se apunta TEST_DATABASE_URL a una base migrada (alembic upgrade head) y sembrada (python -m scripts.seed).
# Sin esa variable las pruebas corren en SQLite, como antes.
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
os.environ["ADMIN_DATABASE_URL"] = TEST_DATABASE_URL or "sqlite+aiosqlite://"
os.environ["ADMIN_JWT_SECRET"] = "secreto-de-test"

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.pool import StaticPool
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.database as database
from app import models
from app.database import Base
from app.main import app as fastapi_app
from app.security import hash_password


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def engine():
    if TEST_DATABASE_URL:
        from sqlalchemy.pool import NullPool
        eng = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
        database._engine = eng
        database._session_factory = async_sessionmaker(eng, expire_on_commit=False)
        yield eng
        await eng.dispose()
        return
    eng = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool,
                              connect_args={"check_same_thread": False})
    database._engine = eng
    database._session_factory = async_sessionmaker(eng, expire_on_commit=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture(scope="session")
async def seeded(engine):
    if TEST_DATABASE_URL:  # la base de prueba ya viene sembrada
        return True
    from scripts.seed import seed
    await seed()
    return True


@pytest_asyncio.fixture
async def client(seeded):
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def admin_headers(client):
    r = await client.post("/api/v1/admin/auth/login",
                          json={"email": "admin@wellq.co.uk", "password": "Admin123!"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest_asyncio.fixture
async def analyst_headers(client):
    r = await client.post("/api/v1/admin/auth/login",
                          json={"email": "analista@wellq.co.uk", "password": "Analista123!"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}
