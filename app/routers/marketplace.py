"""Sección 10 — Catálogos del marketplace."""
from uuid import UUID

from fastapi import APIRouter, Query, Request
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import selectinload

from app import loyalty, models
from app.audit import audit
from app.deps import AppSyncDb, Db, require
from app.enums import CaregiverStatus, ContentStatus
from app.errors import conflict, invalid, not_found
from app.schemas.common import Page
from app.schemas.operation import (CaregiverDetailOut, CaregiverDocumentOut, CaregiverDocumentReviewIn,
                                   CaregiverDocumentSyncIn, CaregiverOut, CaregiverPatchIn,
                                   CaregiverPointsIn, CaregiverPointsOut, CaregiverRankingItem, CaregiverReviewIn,
                                   CaregiverReviewOut, CaregiverReviewSyncIn, CaregiverSyncIn, ProductCreateIn,
                                   ProductOut, ProductPatchIn, ReviewArchiveIn)
from app.security import now_utc

router = APIRouter(prefix="/marketplace", tags=["Catálogos marketplace"])


def _cg_out(c: models.CaregiverProfile, include_note: bool = False) -> CaregiverOut:
    return CaregiverOut(caregiver_id=c.id, name=c.name, zone=c.zone,
                        specialties=c.specialties or [], languages=c.languages or [],
                        certifications_count=c.certifications_count, rating_avg=c.rating_avg,
                        reviews_count=c.reviews_count, status=CaregiverStatus(c.status),
                        submitted_at=c.submitted_at, reviewed_by_name=c.reviewed_by_name,
                        internal_note=c.internal_note if include_note else None,
                        photo_url=c.photo_url)


# ---------- 10.1 Listar cuidadoras ----------
@router.get("/caregivers", response_model=Page[CaregiverOut], dependencies=[require("marketplace")])
async def list_caregivers(db: Db,
                          status_f: CaregiverStatus | None = Query(default=None, alias="status"),
                          zone: str | None = None,
                          specialty: str | None = None,
                          q: str | None = Query(default=None, max_length=120),
                          page: int = Query(default=1, ge=1),
                          page_size: int = Query(default=25, ge=1, le=100)):
    stmt = select(models.CaregiverProfile).options(selectinload(models.CaregiverProfile.admin_users))
    if status_f:
        stmt = stmt.where(models.CaregiverProfile.status == status_f)
    if zone:
        stmt = stmt.where(models.CaregiverProfile.zone.ilike(f"%{zone}%"))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(models.CaregiverProfile.name.ilike(like) |
                          models.CaregiverProfile.email.ilike(like))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.order_by(models.CaregiverProfile.submitted_at.desc())
                             .offset((page - 1) * page_size).limit(page_size))).scalars().all()
    if specialty:  # las especialidades viven en JSON; filtro en memoria sobre la página
        rows = [r for r in rows if specialty.lower() in [s.lower() for s in (r.specialties or [])]]
    return Page(items=[_cg_out(c) for c in rows], page=page, page_size=page_size, total=total)


# ---------- Documentos de respaldo ----------
# Documentos que deben estar aprobados para poder aprobar (o reactivar) el perfil.
REQUIRED_DOCS = ("id_card", "background_check")


async def _documents_for(db: Db, caregiver_id: UUID) -> list[models.CaregiverDocument]:
    return list((await db.execute(
        select(models.CaregiverDocument).options(selectinload(models.CaregiverDocument.reviewer))
        .where(models.CaregiverDocument.caregiver_id == caregiver_id)
        .order_by(models.CaregiverDocument.doc_type))).scalars().all())


def _needs_review(d: models.CaregiverDocument) -> bool:
    return d.status == "pending" or d.pending_file_url is not None


def _required_approved(docs: list[models.CaregiverDocument]) -> bool:
    approved = {d.doc_type for d in docs if d.status == "approved"}
    return all(t in approved for t in REQUIRED_DOCS)


def _doc_out(d: models.CaregiverDocument) -> CaregiverDocumentOut:
    return CaregiverDocumentOut(
        doc_type=d.doc_type, file_url=d.file_url, original_filename=d.original_filename,
        uploaded_at=d.uploaded_at, status=d.status, pending_file_url=d.pending_file_url,
        pending_filename=d.pending_filename, review_note=d.review_note, reviewed_at=d.reviewed_at,
        reviewed_by_name=d.reviewer.full_name if d.reviewer is not None else None)


def _recount(c: models.CaregiverProfile, docs: list[models.CaregiverDocument]) -> None:
    """Mantiene los contadores del perfil (con sus restricciones: verificadas <= total)."""
    verified = sum(1 for d in docs if d.status == "approved")
    c.certifications_count = max(c.certifications_count, len(docs), verified)
    c.certifications_verified_count = verified


def _apply_upload(existing: models.CaregiverDocument | None, caregiver_id: UUID, body: CaregiverDocumentSyncIn,
                  db: Db) -> models.CaregiverDocument:
    """Registra un archivo subido en la app. Si reemplaza a un documento ya aprobado, el nuevo queda como
    cambio pendiente y el aprobado sigue vigente."""
    if existing is None:
        existing = models.CaregiverDocument(caregiver_id=caregiver_id, doc_type=body.doc_type,
                                            file_url=body.file_url, original_filename=body.original_filename,
                                            status="pending")
        db.add(existing)
    elif body.is_change and existing.status == "approved":
        existing.pending_file_url = body.file_url
        existing.pending_filename = body.original_filename
        existing.review_note = None
    else:
        existing.file_url = body.file_url
        existing.original_filename = body.original_filename
        existing.status = "pending"
        existing.review_note = None
        existing.reviewed_at = None
        existing.reviewed_by = None
        existing.pending_file_url = None
        existing.pending_filename = None
    return existing


def _apply_review(d: models.CaregiverDocument, action: str, note: str | None, admin: models.AdminUser) -> None:
    d.reviewed_at = now_utc()
    d.reviewed_by = admin.id
    d.review_note = note
    if action == "approve":
        if d.pending_file_url:  # el cambio propuesto pasa a ser el documento vigente
            d.file_url = d.pending_file_url
            d.original_filename = d.pending_filename or d.original_filename
            d.pending_file_url = None
            d.pending_filename = None
        d.status = "approved"
    elif d.pending_file_url:  # se descarta el cambio; el documento aprobado sigue vigente
        d.pending_file_url = None
        d.pending_filename = None
        d.status = "approved"
    else:
        d.status = "rejected"


# ---------- Sincronización desde la app (servidor a servidor, X-Internal-Key) ----------
@router.post("/caregivers/sync", response_model=CaregiverOut, status_code=201, tags=["Sincronización con la app"])
async def sync_caregiver(body: CaregiverSyncIn, db: AppSyncDb):
    """La cuidadora publica o actualiza su perfil en la app. Llega como «pendiente» para que el staff lo
    revise; si ya existe solo se actualizan los datos descriptivos (el estado lo decide el staff)."""
    c = await db.get(models.CaregiverProfile, body.caregiver_id)
    if c is None:
        c = models.CaregiverProfile(caregiver_id=body.caregiver_id, display_name=body.name, email=body.email,
                                    zone=body.zone, specialties=body.specialties, languages=body.languages,
                                    certifications_count=body.certifications_count, photo_url=body.photo_url,
                                    status="pending", submitted_at=now_utc())
        db.add(c)
    else:
        c.display_name, c.email, c.zone = body.name, body.email, body.zone
        c.specialties, c.languages, c.photo_url = body.specialties, body.languages, body.photo_url
        c.certifications_count = max(body.certifications_count, c.certifications_verified_count)
        c.synced_at = now_utc()
    await db.flush()
    return _cg_out(c)


@router.post("/caregivers/documents/sync", response_model=CaregiverDocumentOut, status_code=201,
             tags=["Sincronización con la app"])
async def sync_document(body: CaregiverDocumentSyncIn, db: AppSyncDb):
    """La cuidadora sube un documento en la app: queda «pendiente de revisión»."""
    c = await db.get(models.CaregiverProfile, body.caregiver_id)
    if c is None:
        raise not_found("La cuidadora aún no está sincronizada: envía primero su perfil.")
    existing = (await db.execute(select(models.CaregiverDocument).where(
        models.CaregiverDocument.caregiver_id == c.id,
        models.CaregiverDocument.doc_type == body.doc_type))).scalar_one_or_none()
    doc = _apply_upload(existing, c.id, body, db)
    await db.flush()
    _recount(c, await _documents_for(db, c.id))
    await db.refresh(doc, attribute_names=["reviewer"])
    return _doc_out(doc)


@router.get("/caregivers/statuses", tags=["Sincronización con la app"])
async def caregiver_statuses(db: AppSyncDb):
    """La app consulta el estado de revisión de cada cuidadora (aprobada, pendiente o suspendida)."""
    rows = (await db.execute(select(models.CaregiverProfile.caregiver_id, models.CaregiverProfile.status))).all()
    return {"items": [{"caregiver_id": str(i), "status": st} for i, st in rows]}


@router.get("/caregivers/documents", tags=["Sincronización con la app"])
async def caregiver_documents_for_app(db: AppSyncDb, caregiver_id: UUID):
    """La app consulta las decisiones del staff sobre los documentos de una cuidadora, por si el aviso no llegó."""
    docs = await _documents_for(db, caregiver_id)
    return {"items": [{"doc_type": d.doc_type, "file_url": d.file_url, "status": d.status,
                       "pending_file_url": d.pending_file_url, "review_note": d.review_note} for d in docs]}


# ---------- Fidelización: reseñas, puntos y ranking ----------
async def _register_review(db: Db, c: models.CaregiverProfile, family_name: str, rating: int, comment: str | None,
                           job_reference: str | None, admin: models.AdminUser | None,
                           external_id: UUID | None = None) -> tuple[models.CaregiverReview, int]:
    """Crea la reseña, otorga (o descuenta) los puntos que correspondan y recalcula la nota de la cuidadora.
    Lo comparten el registro manual del staff y la reseña que llega desde la app (sin staff, `admin` = None)."""
    review = models.CaregiverReview(caregiver_id=c.id, family_name=family_name, rating=rating, comment=comment,
                                    job_reference=job_reference, external_id=external_id)
    db.add(review)
    await db.flush()
    awarded_by = admin.id if admin else None
    awarded = loyalty.POINTS_JOB_COMPLETED
    db.add(models.CaregiverPointsLog(caregiver_id=c.id, points=awarded, reason="job_completed", review_id=review.id,
                                     note=f"Trabajo cerrado con reseña de {family_name}.", awarded_by=awarded_by))
    extra = loyalty.review_bonus(rating)
    if extra is not None:
        pts, reason, note = extra
        awarded += pts
        db.add(models.CaregiverPointsLog(caregiver_id=c.id, points=pts, reason=reason, review_id=review.id,
                                         note=note, awarded_by=awarded_by))
    await db.flush()
    await loyalty.recalc_rating(db, c)
    return review, awarded


def _review_out(r: models.CaregiverReview, points: int) -> CaregiverReviewOut:
    return CaregiverReviewOut(id=r.id, caregiver_id=r.caregiver_id, family_name=r.family_name, rating=r.rating,
                              comment=r.comment, job_reference=r.job_reference, points_awarded=points,
                              created_at=r.created_at, archived_at=r.archived_at,
                              archived_by_name=r.archiver.full_name if r.archiver is not None else None)


@router.get("/caregivers/ranking", response_model=Page[CaregiverRankingItem], dependencies=[require("marketplace")])
async def caregiver_ranking(db: Db, zone: str | None = None, page: int = Query(default=1, ge=1),
                            page_size: int = Query(default=25, ge=1, le=100)):
    """Cuidadoras aprobadas ordenadas por puntos de fidelización (a igual puntaje, por nota)."""
    points = loyalty.points_by_caregiver()
    stmt = (select(models.CaregiverProfile, func.coalesce(points.c.points, 0))
            .outerjoin(points, points.c.caregiver_id == models.CaregiverProfile.caregiver_id)
            .where(models.CaregiverProfile.status == CaregiverStatus.approved))
    if zone:
        stmt = stmt.where(models.CaregiverProfile.zone.ilike(f"%{zone}%"))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    stmt = stmt.order_by(func.coalesce(points.c.points, 0).desc(), models.CaregiverProfile.rating_avg.desc().nulls_last())
    rows = (await db.execute(stmt.offset((page - 1) * page_size).limit(page_size))).all()
    items = [CaregiverRankingItem(rank=(page - 1) * page_size + i + 1, caregiver_id=c.id, name=c.name,
                                  photo_url=c.photo_url, zone=c.zone, points=int(pts),
                                  rating_avg=float(c.rating_avg) if c.rating_avg is not None else None,
                                  reviews_count=c.reviews_count, status=CaregiverStatus(c.status))
             for i, (c, pts) in enumerate(rows)]
    return Page(items=items, page=page, page_size=page_size, total=total)


@router.post("/caregivers/{caregiver_id}/points", response_model=CaregiverPointsOut, status_code=201)
async def award_points(caregiver_id: UUID, body: CaregiverPointsIn, request: Request, db: Db,
                       admin: models.AdminUser = require("marketplace", write=True)):
    """Bono (o descuento) manual de puntos, con una nota que queda en el historial."""
    c = await db.get(models.CaregiverProfile, caregiver_id)
    if c is None:
        raise not_found()
    if c.status != CaregiverStatus.approved:
        raise conflict("CAREGIVER_NOT_APPROVED", "Solo se pueden otorgar puntos a cuidadoras con perfil aprobado.")
    log = models.CaregiverPointsLog(caregiver_id=c.id, points=body.points, reason=body.reason, note=body.note,
                                    awarded_by=admin.id)
    db.add(log)
    await db.flush()
    total = await loyalty.total_points(db, c.id)
    await audit(db, request, "marketplace.points_awarded", "caregiver_profile", c.id,
                after={"points": body.points, "reason": body.reason, "total_points": total})
    return CaregiverPointsOut(caregiver_id=c.id, points_awarded=body.points, reason=body.reason, total_points=total,
                              created_at=log.created_at or now_utc())


@router.post("/reviews", response_model=CaregiverReviewOut, status_code=201)
async def create_review(body: CaregiverReviewIn, request: Request, db: Db,
                        admin: models.AdminUser = require("marketplace", write=True)):
    """El staff registra una reseña (cierra el ciclo de fidelización y mueve puntos y ranking)."""
    c = await db.get(models.CaregiverProfile, body.caregiver_id)
    if c is None:
        raise not_found()
    if c.status != CaregiverStatus.approved:
        raise conflict("CAREGIVER_NOT_APPROVED", "Solo se puede reseñar a cuidadoras con perfil aprobado.")
    review, awarded = await _register_review(db, c, body.family_name, body.rating, body.comment,
                                             body.job_reference, admin)
    await audit(db, request, "marketplace.review_created", "caregiver_profile", c.id,
                after={"rating": body.rating, "points_awarded": awarded})
    await db.refresh(review, attribute_names=["archiver"])
    return _review_out(review, awarded)


@router.post("/reviews/sync", response_model=CaregiverReviewOut, status_code=201, tags=["Sincronización con la app"])
async def sync_review(body: CaregiverReviewSyncIn, request: Request, db: AppSyncDb):
    """La familia reseña a la cuidadora en la app. Es idempotente: reenviar la misma reseña (mismo review_id)
    devuelve la ya registrada sin volver a sumar puntos."""
    c = await db.get(models.CaregiverProfile, body.caregiver_id)
    if c is None:
        raise not_found("La cuidadora aún no está sincronizada.")
    if body.review_id is not None:
        dup = (await db.execute(select(models.CaregiverReview).options(selectinload(models.CaregiverReview.archiver))
                                .where(models.CaregiverReview.external_id == body.review_id))).scalar_one_or_none()
        if dup is not None:
            pts = (await db.execute(select(func.coalesce(func.sum(models.CaregiverPointsLog.points), 0))
                                    .where(models.CaregiverPointsLog.review_id == dup.id))).scalar_one()
            return _review_out(dup, int(pts))
    review, awarded = await _register_review(db, c, body.family_name, body.rating, body.comment,
                                             "Reseña de la app", None, external_id=body.review_id)
    await audit(db, request, "marketplace.review_synced", "caregiver_profile", c.id,
                after={"rating": body.rating, "points_awarded": awarded}, actor=None)
    await db.refresh(review, attribute_names=["archiver"])
    return _review_out(review, awarded)


@router.get("/reviews/moderation", tags=["Sincronización con la app"])
async def reviews_moderation_for_app(db: AppSyncDb):
    """La app consulta qué reseñas suyas el staff archivó o eliminó, para ocultarlas y recalcular la nota."""
    archived = (await db.execute(select(models.CaregiverReview.external_id).where(
        models.CaregiverReview.external_id.is_not(None), models.CaregiverReview.archived_at.is_not(None)))).scalars().all()
    deleted = (await db.execute(text(
        "SELECT DISTINCT external_id FROM admin.marketplace_caregiver_reviews_history "
        "WHERE op = 'D' AND external_id IS NOT NULL"))).scalars().all()
    return {"archived": [str(i) for i in archived], "deleted": [str(i) for i in deleted]}


@router.get("/caregivers/{caregiver_id}/reviews", response_model=Page[CaregiverReviewOut],
            dependencies=[require("marketplace")])
async def list_reviews(caregiver_id: UUID, db: Db, page: int = Query(default=1, ge=1),
                       page_size: int = Query(default=25, ge=1, le=100)):
    """Calificaciones de una cuidadora, una por una (incluye las archivadas, marcadas como tales)."""
    if await db.get(models.CaregiverProfile, caregiver_id) is None:
        raise not_found()
    stmt = (select(models.CaregiverReview).options(selectinload(models.CaregiverReview.archiver))
            .where(models.CaregiverReview.caregiver_id == caregiver_id)
            .order_by(models.CaregiverReview.created_at.desc()))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.offset((page - 1) * page_size).limit(page_size))).scalars().all()
    pts = dict((await db.execute(
        select(models.CaregiverPointsLog.review_id, func.sum(models.CaregiverPointsLog.points))
        .where(models.CaregiverPointsLog.review_id.in_([r.id for r in rows]))
        .group_by(models.CaregiverPointsLog.review_id))).all()) if rows else {}
    return Page(items=[_review_out(r, int(pts.get(r.id, 0))) for r in rows], page=page, page_size=page_size, total=total)


async def _moderate(db: Db, request: Request, admin: models.AdminUser, review_id: UUID, action: str):
    r = await db.get(models.CaregiverReview, review_id, options=[selectinload(models.CaregiverReview.archiver)])
    if r is None:
        raise not_found()
    c = await db.get(models.CaregiverProfile, r.caregiver_id)
    before = {"rating": r.rating, "archived": r.archived_at is not None, "family": r.family_name}
    if action == "archive":
        r.archived_at, r.archived_by = now_utc(), admin.id
    elif action == "restore":
        r.archived_at, r.archived_by = None, None
    else:  # delete: los puntos de la reseña se van con ella (ON DELETE CASCADE)
        await db.delete(r)
    await db.flush()
    await loyalty.recalc_rating(db, c)
    await audit(db, request, f"marketplace.review_{action}", "caregiver_profile", c.id, before=before,
                after={"total_points": await loyalty.total_points(db, c.id)})
    return r


@router.patch("/reviews/{review_id}", response_model=CaregiverReviewOut)
async def archive_review(review_id: UUID, body: ReviewArchiveIn, request: Request, db: Db,
                         admin: models.AdminUser = require("marketplace", write=True)):
    """Archiva (la oculta; deja de contar para la nota y los puntos) o restaura una reseña."""
    r = await _moderate(db, request, admin, review_id, "archive" if body.archived else "restore")
    await db.refresh(r, attribute_names=["archiver"])
    pts = (await db.execute(select(func.coalesce(func.sum(models.CaregiverPointsLog.points), 0))
                            .where(models.CaregiverPointsLog.review_id == r.id))).scalar_one()
    return _review_out(r, int(pts))


@router.delete("/reviews/{review_id}", status_code=204)
async def delete_review(review_id: UUID, request: Request, db: Db,
                        admin: models.AdminUser = require("marketplace", write=True)):
    """Elimina la reseña definitivamente, junto con los puntos que dio o quitó."""
    await _moderate(db, request, admin, review_id, "delete")


# ---------- Eliminar el perfil de una cuidadora ----------
# Documentos, reseñas y puntos se van con ella (ON DELETE CASCADE); el historial conserva quién y cuándo.
@router.delete("/caregivers/{caregiver_id}", status_code=204)
async def delete_caregiver(caregiver_id: UUID, request: Request, db: Db,
                           admin: models.AdminUser = require("marketplace", write=True)):
    """El staff elimina un perfil. La app debe borrar también su cuenta: consulta
    GET /marketplace/caregivers/statuses y ya no la verá."""
    c = await db.get(models.CaregiverProfile, caregiver_id)
    if c is None:
        raise not_found()
    await audit(db, request, "marketplace.caregiver_delete", "caregiver_profile", c.id,
                before={"name": c.name, "email": c.email, "status": c.status})
    await db.delete(c)


@router.post("/caregivers/{caregiver_id}/remove", status_code=204, tags=["Sincronización con la app"])
async def remove_caregiver(caregiver_id: UUID, db: AppSyncDb):
    """La cuidadora eliminó su cuenta en la app: desaparece también de la Consola. Repetirlo no falla."""
    c = await db.get(models.CaregiverProfile, caregiver_id)
    if c is not None:
        await db.delete(c)


# ---------- Ficha de la cuidadora con sus documentos ----------
@router.get("/caregivers/{caregiver_id}", response_model=CaregiverDetailOut, dependencies=[require("marketplace")])
async def get_caregiver(caregiver_id: UUID, db: Db):
    c = await db.get(models.CaregiverProfile, caregiver_id, options=[selectinload(models.CaregiverProfile.admin_users)])
    if c is None:
        raise not_found()
    docs = await _documents_for(db, c.id)
    return CaregiverDetailOut(**_cg_out(c, include_note=True).model_dump(), email=c.email,
                              certifications_verified=_required_approved(docs),
                              pending_documents=sum(1 for d in docs if _needs_review(d)),
                              documents=[_doc_out(d) for d in docs])


@router.post("/caregivers/{caregiver_id}/documents/{doc_type}/review", response_model=CaregiverDocumentOut)
async def review_document(caregiver_id: UUID, doc_type: str, body: CaregiverDocumentReviewIn, request: Request,
                          db: Db, admin: models.AdminUser = require("marketplace", write=True)):
    """El staff aprueba o rechaza un documento. Rechazar exige un motivo; revisar algo ya resuelto da 409."""
    c = await db.get(models.CaregiverProfile, caregiver_id)
    if c is None:
        raise not_found()
    docs = await _documents_for(db, c.id)
    d = next((x for x in docs if x.doc_type == doc_type), None)
    if d is None:
        raise not_found("La cuidadora no ha subido ese documento.")
    if not _needs_review(d):
        raise conflict("NOTHING_TO_REVIEW", "Este documento ya fue revisado y no tiene cambios pendientes.")
    if body.action == "reject" and not body.note:
        raise invalid("NOTE_REQUIRED", "Para rechazar un documento indica el motivo.")
    before = {"status": d.status, "pending": d.pending_file_url is not None}
    _apply_review(d, body.action, body.note, admin)
    _recount(c, docs)
    await db.flush()
    await audit(db, request, "marketplace.document_review", "caregiver_profile", c.id, before=before,
                after={"doc_type": doc_type, "action": body.action, "status": d.status})
    await db.refresh(d, attribute_names=["reviewer"])
    return _doc_out(d)


# ---------- 10.2 Aprobar / suspender / anotar ----------
@router.patch("/caregivers/{caregiver_id}", response_model=CaregiverOut)
async def patch_caregiver(caregiver_id: UUID, body: CaregiverPatchIn, request: Request, db: Db,
                          admin: models.AdminUser = require("marketplace", write=True)):
    c = await db.get(models.CaregiverProfile, caregiver_id)
    if c is None:
        raise not_found()
    before = {"status": c.status}
    if body.status is not None:
        if body.status == CaregiverStatus.approved:
            docs = await _documents_for(db, c.id)
            # Con documentos en el sistema se exigen la cédula y los antecedentes aprobados; los perfiles
            # anteriores a los documentos conservan la regla de al menos una certificación verificada.
            ok = _required_approved(docs) if docs else c.certifications_verified_count >= 1
            if not ok:
                raise conflict("CERTIFICATION_REQUIRED",
                               "No puede aprobarse un perfil sin la cédula y los antecedentes aprobados.")
        if body.status == CaregiverStatus.suspended and not body.reason:
            raise invalid("REASON_REQUIRED", "Para suspender un perfil debes indicar el motivo.")
        c.status = body.status
        c.status_reason = body.reason
        c.reviewed_by = admin.id
        c.reviewed_by_name = admin.full_name
        c.reviewed_at = now_utc()
        # Al suspender se notificaría a la cuidadora con el motivo.
    if body.internal_note is not None:
        c.internal_note = body.internal_note
    await audit(db, request, "marketplace.caregiver_update", "caregiver_profile", c.id,
                before=before, after={"status": c.status})
    return _cg_out(c, include_note=True)


# ---------- 10.3 Listar artículos ----------
@router.get("/products", response_model=Page[ProductOut], dependencies=[require("marketplace")])
async def list_products(db: Db,
                        category: str | None = None,
                        status_f: ContentStatus | None = Query(default=None, alias="status"),
                        q: str | None = Query(default=None, max_length=120),
                        page: int = Query(default=1, ge=1),
                        page_size: int = Query(default=25, ge=1, le=100)):
    stmt = select(models.Product)
    if category:
        stmt = stmt.where(models.Product.category.ilike(f"%{category}%"))
    if status_f:
        stmt = stmt.where(models.Product.status == status_f)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(models.Product.name.ilike(like) | models.Product.vendor.ilike(like))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.order_by(models.Product.updated_at.desc())
                             .offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return Page(items=[ProductOut.model_validate(p) for p in rows],
                page=page, page_size=page_size, total=total)


# ---------- 10.4 Crear artículo ----------
@router.post("/products", response_model=ProductOut, status_code=201)
async def create_product(body: ProductCreateIn, request: Request, db: Db,
                         admin: models.AdminUser = require("marketplace", write=True)):
    p = models.Product(name=body.name, category=body.category, vendor=body.vendor,
                       price_clp=body.price_clp, external_url=str(body.external_url),
                       image_url=str(body.image_url) if body.image_url else None,
                       created_by=admin.id)
    db.add(p)
    await db.flush()
    await db.refresh(p)
    await audit(db, request, "marketplace.product_create", "product", p.id, after={"name": p.name})
    return ProductOut.model_validate(p)


# ---------- 10.5 Editar / archivar artículo ----------
@router.patch("/products/{product_id}", response_model=ProductOut)
async def patch_product(product_id: UUID, body: ProductPatchIn, request: Request, db: Db,
                        admin: models.AdminUser = require("marketplace", write=True)):
    p = await db.get(models.Product, product_id)
    if p is None:
        raise not_found()
    before = {"status": p.status, "name": p.name}
    if body.external_url is not None:
        if body.external_url.scheme != "https":
            raise invalid("INSECURE_URL", "El enlace externo debe usar https.")
        p.external_url = str(body.external_url)
    for field in ("name", "category", "vendor", "price_clp"):
        v = getattr(body, field)
        if v is not None:
            setattr(p, field, v)
    if body.image_url is not None:
        p.image_url = str(body.image_url)
    if body.status is not None:
        p.status = body.status
        if body.status == ContentStatus.published and p.published_at is None:
            p.published_at = now_utc()
        elif body.status == ContentStatus.archived and p.archived_at is None:
            p.archived_at = now_utc()
    p.updated_by = admin.id
    p.updated_at = now_utc()
    await db.flush()
    await db.refresh(p)
    await audit(db, request, "marketplace.product_update", "product", p.id, before=before,
                after={"status": p.status, "name": p.name})
    return ProductOut.model_validate(p)
