"""Sección 3 — Autenticación y gestión del staff."""
from datetime import timedelta
from uuid import UUID, uuid4

from fastapi import APIRouter, Query, Request, Response
from sqlalchemy import func, select, text

from app import models
from app.audit import audit
from app.config import get_settings
from app.deps import CurrentAdmin, Db, permissions_for, require
from app.enums import AdminRole
from app.errors import ApiError, conflict, invalid, not_found, unauthorized
from app.schemas.auth import (AdminCreateIn, AdminCreateOut, AdminPatchIn, AdminUserOut,
                              LoginIn, LoginOut, MeOut, RefreshIn, RefreshOut)
from app.schemas.common import Page
from app.security import (as_utc, create_access_token, hash_refresh, new_refresh_token,
                          now_utc, verify_password, verify_totp)

router = APIRouter(tags=["Autenticación de staff"])


# ---------- 3.1 Login ----------
@router.post("/auth/login", response_model=LoginOut)
async def login(body: LoginIn, request: Request, db: Db):
    s = get_settings()
    q = await db.execute(select(models.AdminUser).where(models.AdminUser.email == body.email.lower()))
    admin = q.scalar_one_or_none()

    async def fail(code="INVALID_CREDENTIALS", msg="Correo o contraseña incorrectos.", status=401):
        if admin is not None:
            db.info["tenant_id"] = admin.tenant_id
            await db.execute(text("SELECT set_config('app.tenant_id', :tid, true)"),
                             {"tid": str(admin.tenant_id)})
            await audit(db, request, "auth.login_failed", "admin_user", admin.id, actor=admin)
            await db.commit()  # persistir el intento fallido y la auditoría pese al error
        raise ApiError(status, code, msg)

    if admin is None:
        await fail()  # mismo error que contraseña incorrecta: no revela existencia
    db.info["tenant_id"] = admin.tenant_id
    await db.execute(text("SELECT set_config('app.tenant_id', :tid, true)"),
                     {"tid": str(admin.tenant_id)})
    await db.execute(text("SELECT set_config('app.actor_id', :aid, true)"),
                     {"aid": str(admin.id)})
    if admin.locked_until and as_utc(admin.locked_until) > now_utc():
        raise ApiError(423, "ACCOUNT_LOCKED", "Cuenta bloqueada 15 minutos por intentos fallidos repetidos.")
    if admin.password_hash is None or not verify_password(body.password, admin.password_hash):
        admin.failed_attempts += 1
        if admin.failed_attempts >= s.max_login_attempts:
            admin.locked_until = now_utc() + timedelta(minutes=s.lockout_minutes)
            admin.failed_attempts = 0
        await fail()
    if not admin.is_active:
        await fail("ADMIN_DISABLED", "Esta cuenta de administración está desactivada. Contacta a un admin.", 403)
    if admin.mfa_enabled:
        if not body.otp_code:
            await fail("OTP_REQUIRED", "Esta cuenta exige segundo factor. Envía tu código TOTP.")
        if not verify_totp(admin.mfa_secret or "", body.otp_code):
            await fail("OTP_INVALID", "El código de verificación no es válido o expiró.")

    admin.failed_attempts = 0
    admin.locked_until = None
    previous_login = admin.last_login_at
    admin.last_login_at = now_utc()

    token, refresh_hash, expires = new_refresh_token()
    db.add(models.AdminSession(admin_id=admin.id, refresh_hash=refresh_hash, expires_at=expires,
                               family_id=uuid4()))
    await audit(db, request, "auth.login", "admin_user", admin.id, actor=admin)
    return LoginOut(access_token=create_access_token(admin.id, admin.role, admin.tenant_id),
                    refresh_token=token, admin=admin)


# ---------- 3.2 Refresh ----------
@router.post("/auth/refresh", response_model=RefreshOut)
async def refresh(body: RefreshIn, db: Db):
    q = await db.execute(select(models.AdminSession)
                         .where(models.AdminSession.refresh_hash == hash_refresh(body.refresh_token)))
    session = q.scalar_one_or_none()
    if session is None or as_utc(session.expires_at) < now_utc():
        raise unauthorized("La sesión no es válida o fue revocada. Inicia sesión de nuevo.", "INVALID_REFRESH")
    db.info["tenant_id"] = session.tenant_id
    await db.execute(text("SELECT set_config('app.tenant_id', :tid, true)"),
                     {"tid": str(session.tenant_id)})
    if session.revoked_at is not None:
        # Reutilización de un token ya rotado: revocar todas las sesiones (posible robo).
        sessions = (await db.execute(select(models.AdminSession)
                                     .where(models.AdminSession.admin_id == session.admin_id,
                                            models.AdminSession.revoked_at.is_(None)))).scalars()
        for s_ in sessions:
            s_.revoked_at = now_utc()
            s_.revoked_reason = "reuse_detected"
        await db.commit()
        raise unauthorized("La sesión no es válida o fue revocada. Inicia sesión de nuevo.", "INVALID_REFRESH")

    admin = await db.get(models.AdminUser, session.admin_id)
    if admin is None or not admin.is_active:
        raise unauthorized("La sesión no es válida o fue revocada. Inicia sesión de nuevo.", "INVALID_REFRESH")

    token, refresh_hash, expires = new_refresh_token()
    new_session = models.AdminSession(admin_id=admin.id, refresh_hash=refresh_hash, expires_at=expires,
                                      family_id=session.family_id, rotated_from=session.id)
    db.add(new_session)
    await db.flush()
    session.revoked_at = now_utc()
    session.revoked_reason = "rotated"
    return RefreshOut(access_token=create_access_token(admin.id, admin.role, admin.tenant_id), refresh_token=token)


# ---------- 3.3 Logout ----------
@router.post("/auth/logout", status_code=204)
async def logout(body: RefreshIn, request: Request, db: Db, admin: CurrentAdmin):
    q = await db.execute(select(models.AdminSession)
                         .where(models.AdminSession.refresh_hash == hash_refresh(body.refresh_token),
                                models.AdminSession.admin_id == admin.id))
    session = q.scalar_one_or_none()
    if session is not None:
        session.revoked_at = now_utc()
        session.revoked_reason = "logout"
    await audit(db, request, "auth.logout", "admin_user", admin.id)
    return Response(status_code=204)


# ---------- 3.4 Me ----------
@router.get("/auth/me", response_model=MeOut)
async def me(admin: CurrentAdmin):
    return MeOut(id=admin.id, full_name=admin.full_name, email=admin.email,
                 role=AdminRole(admin.role), mfa_enabled=admin.mfa_enabled,
                 permissions=permissions_for(AdminRole(admin.role)),
                 last_login_at=admin.last_login_at)


# ---------- 3.5 Crear staff ----------
@router.post("/users", response_model=AdminCreateOut, status_code=201)
async def create_staff(body: AdminCreateIn, request: Request, db: Db,
                       admin: models.AdminUser = require("staff", write=True)):
    s = get_settings()
    email = body.email.lower()
    if email.split("@")[1] not in s.allowed_domains:
        raise invalid("DOMAIN_NOT_ALLOWED", "El dominio del correo no está autorizado para cuentas de administración.")
    exists = (await db.execute(select(models.AdminUser).where(models.AdminUser.email == email))).scalar_one_or_none()
    if exists:
        raise conflict("EMAIL_IN_USE", "Ya existe una cuenta de staff con ese correo.")
    require_mfa = body.require_mfa if body.require_mfa is not None else body.role == AdminRole.admin
    new = models.AdminUser(full_name=body.full_name, email=email, role=body.role,
                           is_active=False,  # pendiente hasta activar
                           mfa_required=require_mfa, mfa_enabled=False)
    db.add(new)
    await db.flush()
    invitation_expires_at = now_utc() + timedelta(hours=24)
    db.add(models.AdminInvitations(
        admin_id=new.id, token_hash=hash_refresh(str(uuid4())),
        expires_at=invitation_expires_at, created_by=admin.id,
    ))
    # Aquí se enviaría el correo de activación (fuera del alcance de esta API).
    await audit(db, request, "staff.create", "admin_user", new.id,
                after={"email": email, "role": body.role})
    return AdminCreateOut(id=new.id, email=new.email, role=AdminRole(new.role),
                          invitation_expires_at=invitation_expires_at)


# ---------- 3.6 Listar staff ----------
@router.get("/users", response_model=Page[AdminUserOut])
async def list_staff(db: Db,
                     admin: models.AdminUser = require("staff"),
                     role: AdminRole | None = None,
                     is_active: bool | None = None,
                     q: str | None = Query(default=None, max_length=120),
                     page: int = Query(default=1, ge=1),
                     page_size: int = Query(default=25, ge=1, le=100)):
    stmt = select(models.AdminUser)
    if role:
        stmt = stmt.where(models.AdminUser.role == role)
    if is_active is not None:
        stmt = stmt.where(models.AdminUser.is_active == is_active)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(models.AdminUser.full_name.ilike(like) | models.AdminUser.email.ilike(like))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.order_by(models.AdminUser.created_at.desc())
                             .offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return Page(items=[AdminUserOut.model_validate(r) for r in rows],
                page=page, page_size=page_size, total=total)


# ---------- 3.7 Actualizar staff ----------
@router.patch("/users/{admin_id}", response_model=AdminUserOut)
async def patch_staff(admin_id: UUID, body: AdminPatchIn, request: Request, db: Db,
                      admin: models.AdminUser = require("staff", write=True)):
    target = await db.get(models.AdminUser, admin_id)
    if target is None:
        raise not_found()
    before = {"full_name": target.full_name, "role": target.role, "is_active": target.is_active}
    if body.is_active is False:
        if target.id == admin.id:
            raise conflict("CANNOT_DISABLE_SELF", "No puedes desactivar tu propia cuenta.")
        actives = (await db.execute(
            select(func.count()).select_from(models.AdminUser)
            .where(models.AdminUser.role == AdminRole.admin,
                   models.AdminUser.is_active.is_(True),
                   models.AdminUser.id != target.id))).scalar_one()
        if target.role == AdminRole.admin and actives == 0:
            raise conflict("LAST_ADMIN", "Debe existir al menos una cuenta activa con rol admin.")
    if body.role and target.role == AdminRole.admin and body.role != AdminRole.admin:
        actives = (await db.execute(
            select(func.count()).select_from(models.AdminUser)
            .where(models.AdminUser.role == AdminRole.admin,
                   models.AdminUser.is_active.is_(True),
                   models.AdminUser.id != target.id))).scalar_one()
        if actives == 0:
            raise conflict("LAST_ADMIN", "Debe existir al menos una cuenta activa con rol admin.")

    if body.full_name is not None:
        target.full_name = body.full_name
    if body.role is not None:
        target.role = body.role
    if body.is_active is not None:
        target.is_active = body.is_active
        if body.is_active is False:  # revocar sesiones
            sessions = (await db.execute(select(models.AdminSession)
                                         .where(models.AdminSession.admin_id == target.id,
                                                models.AdminSession.revoked_at.is_(None)))).scalars()
            for s_ in sessions:
                s_.revoked_at = now_utc()
                s_.revoked_reason = "admin_disabled"
    await audit(db, request, "staff.update", "admin_user", target.id, before=before,
                after={"full_name": target.full_name, "role": target.role, "is_active": target.is_active})
    return AdminUserOut.model_validate(target)
