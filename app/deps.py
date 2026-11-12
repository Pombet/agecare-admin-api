"""Dependencias de seguridad: administrador autenticado y control por rol."""
from typing import Annotated
from uuid import UUID

import hmac

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.database import get_db
from app.enums import READ, WRITE, AdminRole
from app.config import get_settings
from app.errors import ApiError, forbidden, unauthorized
from app.security import decode_access_token


bearer_auth = HTTPBearer(
    auto_error=False,
    scheme_name="BearerAuth",
    description="Pega únicamente el access_token devuelto por POST /auth/login.",
)


async def get_current_admin(request: Request,
                            db: Annotated[AsyncSession, Depends(get_db)],
                            credentials: Annotated[HTTPAuthorizationCredentials | None,
                                                   Depends(bearer_auth)]) -> models.AdminUser:
    if credentials is None:
        raise unauthorized()
    try:
        payload = decode_access_token(credentials.credentials.strip())
    except jwt.PyJWTError:
        raise unauthorized()
    try:
        tenant_id = UUID(payload["tid"])
        admin_id = UUID(payload["sub"])
    except (KeyError, TypeError, ValueError):
        raise unauthorized()
    await db.execute(text("SELECT set_config('app.tenant_id', :tid, true)"),
                     {"tid": str(tenant_id)})
    db.info["tenant_id"] = tenant_id
    admin = await db.get(models.AdminUser, admin_id)
    if admin is None or not admin.is_active:
        raise unauthorized()
    if admin.tenant_id != tenant_id:
        raise unauthorized()
    await db.execute(
        text("SELECT set_config('app.actor_id', :aid, true)"),
        {"aid": str(admin.id)},
    )
    db.info["actor_id"] = admin.id
    request.state.actor = admin
    return admin


CurrentAdmin = Annotated[models.AdminUser, Depends(get_current_admin)]
Db = Annotated[AsyncSession, Depends(get_db)]


async def get_app_sync_db(request: Request, db: Annotated[AsyncSession, Depends(get_db)]) -> AsyncSession:
    """Quien llama es la app (otro servicio, sin sesión de staff): se autentica con X-Internal-Key y sus
    escrituras quedan dentro del tenant configurado en ADMIN_INTERNAL_SYNC_TENANT, igual que las del staff."""
    s = get_settings()
    if not s.internal_sync_key:
        raise ApiError(503, "SYNC_DISABLED", "La sincronización con la app no está configurada.")
    sent = request.headers.get("X-Internal-Key", "")
    if not hmac.compare_digest(sent.encode(), s.internal_sync_key.encode()):
        raise forbidden("Clave interna inválida.")
    tenant = (await db.execute(select(models.Tenants).where(models.Tenants.code == s.internal_sync_tenant,
                                                          models.Tenants.is_active.is_(True)))).scalar_one_or_none()
    if tenant is None:
        raise ApiError(503, "SYNC_TENANT_NOT_FOUND", "El tenant de sincronización no existe o está inactivo.")
    await db.execute(text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": str(tenant.id)})
    db.info["tenant_id"] = tenant.id
    return db


AppSyncDb = Annotated[AsyncSession, Depends(get_app_sync_db)]


def require(module: str, write: bool = False):
    """Factoría de dependencia: exige acceso de lectura o escritura sobre un módulo."""
    allowed = WRITE.get(module, set()) if write else READ.get(module, set())

    async def checker(admin: CurrentAdmin) -> models.AdminUser:
        if AdminRole(admin.role) not in allowed:
            raise forbidden()
        return admin

    return Depends(checker)


def permissions_for(role: AdminRole) -> list[str]:
    """Módulos accesibles (lectura) para un rol; alimenta el menú de la consola."""
    return sorted(m for m, roles in READ.items() if role in roles)
