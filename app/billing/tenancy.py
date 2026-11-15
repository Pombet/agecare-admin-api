"""Contexto de tenant para la pasarela de pago.

Las tablas admin.billing_* tienen RLS forzado por tenant (igual que el resto del esquema). La consola
fija el tenant en `get_current_admin`; este módulo resuelve los otros dos casos:

  · la app (usuarios finales con JWT de la app y el retorno de Webpay): tenant configurado en
    ADMIN_BILLING_APP_TENANT (por defecto el mismo de ADMIN_INTERNAL_SYNC_TENANT);
  · el cron diario: recorre los tenants activos y corre el ciclo dentro de cada uno.

`set_config(..., true)` solo vale hasta el fin de la transacción, y el servicio de cobros hace COMMIT
antes de llamar a Transbank. Por eso, en las sesiones marcadas con `info["tenant_scope"]` el listener
`after_begin` vuelve a fijar app.tenant_id / app.actor_id al comenzar cada transacción nueva.

Además, en esas sesiones toda consulta ORM sobre las tablas que usa la pasarela (billing_*, métricas
comerciales y system_settings) lleva el filtro `tenant_id = <tenant>` explícito. Así el aislamiento no
depende solo de RLS: el dueño del esquema queda exento en las tablas sin FORCE (metrics_*) y un rol con
BYPASSRLS lo estaría en todas.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import Depends
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria

from app import models
from app.config import get_settings
from app.database import get_db, get_session_factory
from app.errors import ApiError

TENANT_SCOPE = "tenant_scope"


@event.listens_for(Session, "after_begin")
def _reapply_tenant(session: Session, _transaction, connection) -> None:
    if not session.info.get(TENANT_SCOPE):
        return
    tenant_id = session.info.get("tenant_id")
    if tenant_id is not None:
        connection.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": str(tenant_id)})
    actor_id = session.info.get("actor_id")
    if actor_id is not None:
        connection.execute(text("SELECT set_config('app.actor_id', :aid, true)"), {"aid": str(actor_id)})


def _scoped_models() -> tuple:
    from app.billing.models import BillingEvent, Charge, PaymentMethod, Refund, Subscription
    return (PaymentMethod, Subscription, Charge, Refund, BillingEvent,
            models.MetricsPlanSnapshot, models.MetricsDailyUsers, models.SystemSettings)


@event.listens_for(Session, "do_orm_execute")
def _filter_by_tenant(state: ORMExecuteState) -> None:
    if not state.session.info.get(TENANT_SCOPE):
        return
    tenant_id = state.session.info.get("tenant_id")
    if tenant_id is None or not (state.is_select or state.is_update or state.is_delete):
        return
    if state.is_column_load or state.is_relationship_load:
        return
    state.statement = state.statement.options(*(
        with_loader_criteria(model, model.tenant_id == tenant_id, include_aliases=True)
        for model in _scoped_models()))


async def bind_tenant(db: AsyncSession, tenant_id: UUID) -> AsyncSession:
    """Fija el tenant en la transacción actual y en las siguientes de esta sesión."""
    await db.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": str(tenant_id)})
    db.info["tenant_id"] = tenant_id
    db.info[TENANT_SCOPE] = True
    return db


def keep_tenant(db: AsyncSession) -> AsyncSession:
    """Sesión ya ligada a un tenant (consola): mantenerlo tras los COMMIT intermedios."""
    db.info[TENANT_SCOPE] = True
    return db


def tenant_id_of(db: AsyncSession) -> UUID:
    tenant_id = db.info.get("tenant_id")
    if tenant_id is None:
        raise RuntimeError("Sesión de facturación sin tenant: usar BillingAppDb, bind_tenant o la sesión del staff.")
    return tenant_id


async def get_setting_value(db: AsyncSession, key: str):
    """Valor vigente de un parámetro (system_settings) del tenant de la sesión, o None."""
    setting = await db.get(models.SystemSetting, (tenant_id_of(db), key))
    return setting.value if setting else None


async def app_tenant_id(db: AsyncSession) -> UUID:
    s = get_settings()
    code = s.billing_app_tenant or s.internal_sync_tenant
    tenant = (await db.execute(select(models.Tenants).where(
        models.Tenants.code == code, models.Tenants.is_active.is_(True)))).scalar_one_or_none()
    if tenant is None:
        raise ApiError(503, "BILLING_TENANT_NOT_FOUND",
                       "El tenant de pagos de la app no existe o está inactivo (ADMIN_BILLING_APP_TENANT).")
    return tenant.id


async def get_billing_app_db(db: Annotated[AsyncSession, Depends(get_db)]) -> AsyncSession:
    """Sesión para los endpoints de la app y el retorno de Webpay, dentro del tenant de la app."""
    return await bind_tenant(db, await app_tenant_id(db))


BillingAppDb = Annotated[AsyncSession, Depends(get_billing_app_db)]


async def active_tenants() -> list[tuple[UUID, str]]:
    async with get_session_factory()() as db:
        rows = (await db.execute(select(models.Tenants.id, models.Tenants.code)
                                 .where(models.Tenants.is_active.is_(True))
                                 .order_by(models.Tenants.code))).all()
    return [(r[0], r[1]) for r in rows]


async def tenant_sessions() -> AsyncIterator[tuple[str, AsyncSession]]:
    """Una sesión por tenant activo, ya ligada a él (cron y scripts)."""
    for tenant_id, code in await active_tenants():
        async with get_session_factory()() as db:
            await bind_tenant(db, tenant_id)
            yield code, db
