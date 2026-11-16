"""Aislamiento por tenant de la pasarela: RLS forzado + filtro explícito, cron por tenant."""
import uuid

import pytest
from sqlalchemy import func, select

import app.database as database
from app import models
from app.billing.jobs import run_billing_cycle
from app.billing.metrics_sync import sync_commercial_metrics
from app.billing.models import Charge, Subscription
from app.billing.tenancy import bind_tenant
from app.config import get_settings
from tests.billing.billing_helpers import subscribe_with_card

pytestmark = pytest.mark.asyncio


async def _other_tenant() -> uuid.UUID:
    async with database.get_session_factory()() as db:
        tenant = (await db.execute(select(models.Tenants).where(models.Tenants.code == "billing-otro"))
                  ).scalar_one_or_none()
        if tenant is None:
            tenant = models.Tenants(code="billing-otro", name="Tenant de prueba (pagos)", country_code="CL",
                                    currency_code="CLP", timezone="America/Santiago", environment="development")
            db.add(tenant)
            await db.commit()
        return tenant.id


async def test_other_tenant_sees_nothing_and_its_cycle_charges_nothing(client, fake_gw, app_headers, session):
    data, _ = await subscribe_with_card(client, app_headers(), "gold")
    sid = uuid.UUID(data["subscription"]["id"])
    assert await session.get(Subscription, sid) is not None          # visible en el tenant de la app

    other = await _other_tenant()
    async with database.get_session_factory()() as db:
        await bind_tenant(db, other)
        assert await db.get(Subscription, sid) is None
        assert (await db.execute(select(func.count()).select_from(Subscription))).scalar_one() == 0
        assert (await db.execute(select(func.count()).select_from(Charge))).scalar_one() == 0
        stats = await run_billing_cycle(db, fake_gw)
        await db.commit()
        assert stats.renewed == stats.failed == stats.paying_units == 0 and not stats.errors
        synced = await sync_commercial_metrics(db)
        assert synced.paying_units == 0 and synced.mrr_clp == 0
        await db.rollback()


async def test_cron_runs_once_per_active_tenant(client, fake_gw, monkeypatch):
    await _other_tenant()
    monkeypatch.setattr(get_settings(), "cron_secret", "cron-de-prueba")
    url = "/api/v1/billing/internal/cron/billing-cycle"
    assert (await client.get(url)).status_code == 401
    r = await client.get(url, headers={"Authorization": "Bearer cron-de-prueba"})
    assert r.status_code == 200, r.text
    tenants = r.json()["tenants"]
    assert {"local", "billing-otro"} <= set(tenants)
    assert all(t["errors"] == [] for t in tenants.values())
    assert tenants["billing-otro"]["paying_units"] == 0
    monkeypatch.setattr(get_settings(), "cron_secret", "")
    assert (await client.get(url, headers={"Authorization": "Bearer cron-de-prueba"})).status_code == 404
