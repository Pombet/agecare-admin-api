"""Fixtures de la pasarela de pago (se suman a las de tests/conftest.py).

Requieren PostgreSQL (TEST_DATABASE_URL) migrado hasta 0011_billing y sembrado: las tablas billing_*
usan RLS, triggers y tipos de PostgreSQL. Sin TEST_DATABASE_URL se omiten.
"""
import asyncio
import uuid

import pytest
import pytest_asyncio

import app.database as database
from tests.conftest import TEST_DATABASE_URL

if not TEST_DATABASE_URL:
    collect_ignore_glob = ["test_*.py"]


@pytest_asyncio.fixture
async def fake_gw():
    """Simulador de Transbank limpio para cada test."""
    from app.billing.gateway import FakeGateway, set_gateway
    gw = FakeGateway()
    set_gateway(gw)
    yield gw
    set_gateway(None)


@pytest.fixture
def app_headers():
    """Fábrica de cabeceras con JWT de usuario de la app (único por test si no se indica correo)."""
    from scripts.app_token import make_token

    def make(email: str | None = None, family_ids: list[str] | None = None, provider_id: str | None = None,
             user_id: str | None = None) -> dict:
        email = email or f"user-{uuid.uuid4().hex[:10]}@demo.cl"
        return {"Authorization": f"Bearer {make_token(email, user_id=user_id, family_ids=family_ids, provider_id=provider_id)}"}
    return make


async def _login(client, email: str, password: str) -> dict:
    r = await client.post("/api/v1/admin/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest_asyncio.fixture
async def support_headers(client):
    return await _login(client, "soporte@wellq.co.uk", "Soporte123!")


@pytest_asyncio.fixture
async def editor_headers(client):
    return await _login(client, "editora@wellq.co.uk", "Editora123!")


@pytest_asyncio.fixture
async def session(seeded):
    """Sesión directa a la BD dentro del tenant de la app (el mismo de la consola en el seed: local)."""
    from app.billing.tenancy import app_tenant_id, bind_tenant
    async with database.get_session_factory()() as s:
        await bind_tenant(s, await app_tenant_id(s))
        yield s


@pytest.fixture
def sync_metrics(monkeypatch):
    """Activa ADMIN_BILLING_SYNC_METRICS solo para el test que lo pide."""
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "billing_sync_metrics", True)


METRIC_TABLES = {"admin.metrics_plan_snapshot": "as_of", "admin.metrics_daily_users": "day"}


async def _metrics(action: str, saved: dict | None = None) -> dict:
    import asyncpg
    conn = await asyncpg.connect(TEST_DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        if action == "save":
            return {t: [dict(r) for r in await conn.fetch(f"SELECT * FROM {t} WHERE {c} >= current_date - 1")]
                    for t, c in METRIC_TABLES.items()}
        async with conn.transaction():
            for table, col in METRIC_TABLES.items():
                await conn.execute(f"DELETE FROM {table} WHERE {col} >= current_date - 1")
                rows = saved[table]
                if rows:
                    cols = list(rows[0])
                    await conn.executemany(
                        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(f'${i + 1}' for i in range(len(cols)))})",
                        [tuple(r[c] for c in cols) for r in rows])
        return {}
    finally:
        await conn.close()


@pytest.fixture(scope="package", autouse=True)
def restore_dashboard_metrics():
    """Las pruebas de la pasarela corren el ciclo con fechas simuladas y pueden escribir el dashboard
    comercial: al terminar el paquete se dejan las métricas de los demás tests como estaban."""
    saved = asyncio.run(_metrics("save"))
    yield
    asyncio.run(_metrics("restore", saved))
