"""PostgreSQL 16 desechable, migraciones reales y aislamiento por test."""
import os
from pathlib import Path
import subprocess
import sys

# Nunca heredar la base demo/remota ni TLS de la configuración del desarrollador.
os.environ["ADMIN_DATABASE_URL"] = "postgresql+asyncpg://test:test@127.0.0.1:1/agecare_test"
os.environ["ADMIN_DATABASE_SSL"] = "false"
os.environ["ADMIN_JWT_SECRET"] = "secreto-exclusivo-de-tests-postgresql"

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app import database
from app.config import get_settings
from app.main import app as fastapi_app

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def postgres_url():
    # Importación diferida: las pruebas unitarias no necesitan Docker.
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine", username="test", password="test",
                           dbname="agecare_test", driver=None) as postgres:
        url = ("postgresql+asyncpg://test:test@"
               f"{postgres.get_container_host_ip()}:{postgres.get_exposed_port(5432)}/agecare_test")
        environment = {**os.environ, "ADMIN_DATABASE_URL": url}
        for command in ([sys.executable, "-m", "alembic", "upgrade", "head"],
                        [sys.executable, "-m", "scripts.seed"]):
            result = subprocess.run(command, cwd=ROOT, env=environment,
                                    capture_output=True, text=True, timeout=180)
            if result.returncode:
                pytest.fail(f"Preparación PostgreSQL fallida: {command}\n"
                            f"{result.stdout}\n{result.stderr}")
        yield url


@pytest_asyncio.fixture
async def engine(postgres_url):
    engine = create_async_engine(postgres_url, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def db_connection(engine):
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            yield connection
        finally:
            if transaction.is_active:
                await transaction.rollback()


@pytest_asyncio.fixture
async def client(db_connection, monkeypatch):
    # Los commit de los endpoints solo liberan SAVEPOINT; el test revierte todo.
    factory = async_sessionmaker(db_connection, expire_on_commit=False,
                                join_transaction_mode="create_savepoint")
    monkeypatch.setattr(database, "_session_factory", factory)
    monkeypatch.setattr(database, "get_session_factory", lambda: factory)
    get_settings.cache_clear()
    transport = ASGITransport(app=fastapi_app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        get_settings.cache_clear()


@pytest_asyncio.fixture
async def admin_headers(client):
    response = await client.post("/api/v1/admin/auth/login",
                                 json={"email": "admin@wellq.co.uk", "password": "Admin123!"})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest_asyncio.fixture
async def analyst_headers(client):
    response = await client.post("/api/v1/admin/auth/login",
                                 json={"email": "analista@wellq.co.uk", "password": "Analista123!"})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}
