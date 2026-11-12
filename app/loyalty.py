"""Reglas de fidelización: cuántos puntos da (o quita) cada reseña y cómo se calculan los totales.

El total de una cuidadora es la suma de su historial, sin contar los puntos de reseñas archivadas por el
staff (así archivar una reseña deshace su efecto y restaurarla lo devuelve)."""
from decimal import Decimal

from sqlalchemy import func, or_, select

from app import models

POINTS_JOB_COMPLETED = 10      # al cerrar un trabajo con reseña
POINTS_GOOD_REVIEW_BONUS = 5   # reseña de 4 o 5 estrellas
GOOD_REVIEW_THRESHOLD = 4
# Las reseñas malas restan: una de 2 estrellas anula la mitad del trabajo; una de 1 estrella lo deja en negativo.
BAD_REVIEW_PENALTY = {2: -5, 1: -15}


def review_bonus(rating: int) -> tuple[int, str, str] | None:
    """(puntos, motivo, nota) que suma o resta la calificación, o None si es neutra (3 estrellas)."""
    if rating >= GOOD_REVIEW_THRESHOLD:
        return POINTS_GOOD_REVIEW_BONUS, "good_review", f"Bono por buena reseña (rating {rating})."
    penalty = BAD_REVIEW_PENALTY.get(rating)
    if penalty is not None:
        return penalty, "bad_review", f"Descuento por mala reseña (rating {rating})."
    return None


def active_points():
    """Condición SQL: puntos que cuentan (los sueltos y los de reseñas no archivadas)."""
    archived = select(models.CaregiverReview.id).where(models.CaregiverReview.archived_at.is_not(None))
    return or_(models.CaregiverPointsLog.review_id.is_(None),
               models.CaregiverPointsLog.review_id.not_in(archived))


def points_by_caregiver():
    """Subconsulta (caregiver_id, points) con el total vigente de cada cuidadora."""
    return (select(models.CaregiverPointsLog.caregiver_id,
                   func.sum(models.CaregiverPointsLog.points).label("points"))
            .where(active_points())
            .group_by(models.CaregiverPointsLog.caregiver_id).subquery())


async def total_points(db, caregiver_id) -> int:
    return int((await db.execute(
        select(func.coalesce(func.sum(models.CaregiverPointsLog.points), 0))
        .where(models.CaregiverPointsLog.caregiver_id == caregiver_id, active_points()))).scalar_one())


async def recalc_rating(db, c: models.CaregiverProfile) -> None:
    """Promedio y cantidad de reseñas vigentes (las archivadas no cuentan)."""
    n, avg = (await db.execute(
        select(func.count(models.CaregiverReview.id), func.avg(models.CaregiverReview.rating))
        .where(models.CaregiverReview.caregiver_id == c.id,
               models.CaregiverReview.archived_at.is_(None)))).one()
    c.reviews_count = int(n or 0)
    c.rating_avg = Decimal(str(round(float(avg), 2))) if n else None
