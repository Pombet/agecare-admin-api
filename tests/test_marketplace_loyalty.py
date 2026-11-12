"""Fidelización: las reseñas suman o restan puntos y el staff modera las calificaciones de cada cuidadora.

Necesitan PostgreSQL real (esquema admin, RLS, historial): ver TEST_DATABASE_URL en tests/conftest.py.
"""
import os
from uuid import uuid4

import pytest

from app.config import get_settings

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"),
                                reason="requiere PostgreSQL (TEST_DATABASE_URL)")

BASE = "/api/v1/admin/marketplace"
KEY = {"X-Internal-Key": "clave-de-pruebas"}


@pytest.fixture(autouse=True)
def sync_enabled(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "internal_sync_key", "clave-de-pruebas")
    monkeypatch.setattr(s, "internal_sync_tenant", "local")


async def approved_caregiver(client, admin_headers):
    cid = str(uuid4())
    r = await client.post(f"{BASE}/caregivers/sync", headers=KEY, json={
        "caregiver_id": cid, "name": "Cuidadora de Prueba", "email": f"c{uuid4().hex[:8]}@cuidado.cl", "zone": "Ñuñoa",
        "specialties": [], "languages": ["Español"], "certifications_count": 0})
    assert r.status_code == 201, r.text
    for t in ("id_card", "background_check"):
        r = await client.post(f"{BASE}/caregivers/documents/sync", headers=KEY, json={
            "caregiver_id": cid, "doc_type": t, "file_url": f"https://files.example/{uuid4().hex}.png",
            "original_filename": f"{t}.png"})
        assert r.status_code == 201, r.text
        r = await client.post(f"{BASE}/caregivers/{cid}/documents/{t}/review", headers=admin_headers, json={"action": "approve"})
        assert r.status_code == 200, r.text
    r = await client.patch(f"{BASE}/caregivers/{cid}", headers=admin_headers, json={"status": "approved"})
    assert r.status_code == 200, r.text
    return cid


async def add_review(client, headers, cid, rating, family="Familia Prueba"):
    r = await client.post(f"{BASE}/reviews", headers=headers,
                          json={"caregiver_id": cid, "family_name": family, "rating": rating, "comment": "x"})
    assert r.status_code == 201, r.text
    return r.json()


async def ranking_row(client, headers, cid):
    r = await client.get(f"{BASE}/caregivers/ranking?page_size=100", headers=headers)
    assert r.status_code == 200, r.text
    return next((i for i in r.json()["items"] if i["caregiver_id"] == cid), None)


async def reviews(client, headers, cid):
    r = await client.get(f"{BASE}/caregivers/{cid}/reviews", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["items"]


@pytest.mark.parametrize("rating,awarded", [(5, 15), (4, 15), (3, 10), (2, 5), (1, -5)])
async def test_each_rating_adds_or_removes_points(client, admin_headers, rating, awarded):
    cid = await approved_caregiver(client, admin_headers)
    review = await add_review(client, admin_headers, cid, rating)
    assert review["points_awarded"] == awarded
    assert (await ranking_row(client, admin_headers, cid))["points"] == awarded


async def test_bad_reviews_push_a_caregiver_down_the_ranking(client, admin_headers):
    good = await approved_caregiver(client, admin_headers)
    bad = await approved_caregiver(client, admin_headers)
    await add_review(client, admin_headers, good, 5)
    await add_review(client, admin_headers, bad, 1)
    await add_review(client, admin_headers, bad, 1, "Otra Familia")
    r = (await client.get(f"{BASE}/caregivers/ranking?page_size=100", headers=admin_headers)).json()["items"]
    order = [i["caregiver_id"] for i in r]
    assert order.index(good) < order.index(bad)
    assert (await ranking_row(client, admin_headers, bad))["points"] == -10
    assert [i["rank"] for i in r] == list(range(1, len(r) + 1))


async def test_only_approved_caregivers_can_be_reviewed_or_receive_points(client, admin_headers):
    cid = str(uuid4())
    await client.post(f"{BASE}/caregivers/sync", headers=KEY, json={
        "caregiver_id": cid, "name": "Sin Aprobar", "email": "sin@aprobar.cl", "zone": "Maipú"})
    r = await client.post(f"{BASE}/reviews", headers=admin_headers,
                          json={"caregiver_id": cid, "family_name": "Familia", "rating": 5})
    assert r.status_code == 409 and r.json()["error"]["code"] == "CAREGIVER_NOT_APPROVED"
    r = await client.post(f"{BASE}/caregivers/{cid}/points", headers=admin_headers, json={"points": 10})
    assert r.status_code == 409
    assert (await client.post(f"{BASE}/reviews", headers=admin_headers,
                              json={"caregiver_id": str(uuid4()), "family_name": "Familia", "rating": 5})).status_code == 404


async def test_manual_points_can_add_and_subtract(client, admin_headers):
    cid = await approved_caregiver(client, admin_headers)
    r = await client.post(f"{BASE}/caregivers/{cid}/points", headers=admin_headers, json={"points": 40, "note": "Bono"})
    assert r.status_code == 201 and r.json()["total_points"] == 40
    r = await client.post(f"{BASE}/caregivers/{cid}/points", headers=admin_headers,
                          json={"points": -15, "reason": "bad_review", "note": "Falta reiterada"})
    assert r.json()["total_points"] == 25
    assert (await client.post(f"{BASE}/caregivers/{cid}/points", headers=admin_headers, json={"points": 0})).status_code == 422
    assert (await client.post(f"{BASE}/caregivers/{cid}/points", headers=admin_headers, json={"points": 5000})).status_code == 422


async def test_archiving_a_review_undoes_its_effect_and_restoring_brings_it_back(client, admin_headers):
    cid = await approved_caregiver(client, admin_headers)
    await add_review(client, admin_headers, cid, 5)
    bad = await add_review(client, admin_headers, cid, 1, "Familia Enojada")
    assert (await ranking_row(client, admin_headers, cid))["points"] == 10
    assert (await client.get(f"{BASE}/caregivers/{cid}", headers=admin_headers)).json()["rating_avg"] == 3.0

    r = await client.patch(f"{BASE}/reviews/{bad['id']}", headers=admin_headers, json={"archived": True})
    assert r.status_code == 200 and r.json()["archived_at"] and r.json()["archived_by_name"]
    row = await ranking_row(client, admin_headers, cid)
    assert row["points"] == 15 and row["rating_avg"] == 5.0 and row["reviews_count"] == 1
    items = await reviews(client, admin_headers, cid)
    assert len(items) == 2 and sum(1 for i in items if i["archived_at"]) == 1  # el staff sigue viéndola

    r = await client.patch(f"{BASE}/reviews/{bad['id']}", headers=admin_headers, json={"archived": False})
    assert r.json()["archived_at"] is None
    row = await ranking_row(client, admin_headers, cid)
    assert row["points"] == 10 and row["reviews_count"] == 2


async def test_deleting_a_review_removes_it_and_its_points(client, admin_headers, analyst_headers):
    cid = await approved_caregiver(client, admin_headers)
    keep = await add_review(client, admin_headers, cid, 4)
    gone = await add_review(client, admin_headers, cid, 2, "Familia Injusta")
    assert (await ranking_row(client, admin_headers, cid))["points"] == 20
    assert (await client.delete(f"{BASE}/reviews/{gone['id']}", headers=analyst_headers)).status_code == 403
    assert (await client.delete(f"{BASE}/reviews/{gone['id']}", headers=admin_headers)).status_code == 204
    assert (await ranking_row(client, admin_headers, cid))["points"] == 15
    assert [i["id"] for i in await reviews(client, admin_headers, cid)] == [keep["id"]]
    assert (await client.delete(f"{BASE}/reviews/{gone['id']}", headers=admin_headers)).status_code == 404
    assert (await client.patch(f"{BASE}/reviews/{uuid4()}", headers=admin_headers, json={"archived": True})).status_code == 404


async def test_read_only_staff_cannot_moderate(client, admin_headers, analyst_headers):
    cid = await approved_caregiver(client, admin_headers)
    review = await add_review(client, admin_headers, cid, 1)
    for method, path, body in (("PATCH", f"/reviews/{review['id']}", {"archived": True}),
                               ("POST", "/reviews", {"caregiver_id": cid, "family_name": "Familia", "rating": 5}),
                               ("POST", f"/caregivers/{cid}/points", {"points": 5})):
        assert (await client.request(method, BASE + path, headers=analyst_headers, json=body)).status_code == 403
    assert (await client.get(f"{BASE}/caregivers/ranking", headers={})).status_code == 401


async def test_review_from_the_app_is_idempotent_and_the_app_can_read_the_moderation(client, admin_headers):
    cid = await approved_caregiver(client, admin_headers)
    app_id = str(uuid4())
    body = {"caregiver_id": cid, "family_name": "Familia App", "rating": 2, "comment": "Llegó tarde", "review_id": app_id}
    assert (await client.post(f"{BASE}/reviews/sync", json=body)).status_code == 403
    r = await client.post(f"{BASE}/reviews/sync", headers=KEY, json=body)
    assert r.status_code == 201 and r.json()["points_awarded"] == 5
    again = await client.post(f"{BASE}/reviews/sync", headers=KEY, json=body)  # reintento de la app
    assert again.status_code == 201 and again.json()["id"] == r.json()["id"]
    assert (await ranking_row(client, admin_headers, cid))["points"] == 5  # no se sumó dos veces
    assert (await client.post(f"{BASE}/reviews/sync", headers=KEY, json={**body, "caregiver_id": str(uuid4())})).status_code == 404

    mod = (await client.get(f"{BASE}/reviews/moderation", headers=KEY)).json()
    assert app_id not in mod["archived"] and app_id not in mod["deleted"]
    await client.patch(f"{BASE}/reviews/{r.json()['id']}", headers=admin_headers, json={"archived": True})
    assert app_id in (await client.get(f"{BASE}/reviews/moderation", headers=KEY)).json()["archived"]
    await client.delete(f"{BASE}/reviews/{r.json()['id']}", headers=admin_headers)
    mod = (await client.get(f"{BASE}/reviews/moderation", headers=KEY)).json()
    assert app_id in mod["deleted"] and app_id not in mod["archived"]
    assert (await client.get(f"{BASE}/reviews/moderation")).status_code == 403


async def test_removing_a_caregiver_removes_her_reviews_and_points(client, admin_headers):
    """Las claves foráneas en cascada evitan que queden reseñas o puntos huérfanos."""
    from sqlalchemy import text

    import app.database as database
    cid = await approved_caregiver(client, admin_headers)
    await add_review(client, admin_headers, cid, 5)
    async with database.get_engine().begin() as conn:
        await conn.execute(text("DELETE FROM admin.marketplace_caregivers WHERE caregiver_id = :c"), {"c": cid})
        left = (await conn.execute(text(
            "SELECT (SELECT count(*) FROM admin.marketplace_caregiver_reviews WHERE caregiver_id = :c) + "
            "(SELECT count(*) FROM admin.marketplace_caregiver_points_log WHERE caregiver_id = :c) + "
            "(SELECT count(*) FROM admin.marketplace_caregiver_documents WHERE caregiver_id = :c)"), {"c": cid})).scalar_one()
    assert left == 0


async def test_staff_can_delete_a_caregiver_and_everything_goes_with_her(client, admin_headers, analyst_headers):
    cid = await approved_caregiver(client, admin_headers)
    review = await client.post(f"{BASE}/reviews/sync", headers=KEY, json={
        "caregiver_id": cid, "family_name": "Familia App", "rating": 5, "review_id": str(uuid4())})
    assert review.status_code == 201
    assert (await client.delete(f"{BASE}/caregivers/{cid}", headers=analyst_headers)).status_code == 403
    assert (await client.delete(f"{BASE}/caregivers/{cid}", headers=admin_headers)).status_code == 204
    assert (await client.get(f"{BASE}/caregivers/{cid}", headers=admin_headers)).status_code == 404
    assert (await client.delete(f"{BASE}/caregivers/{cid}", headers=admin_headers)).status_code == 404
    assert await ranking_row(client, admin_headers, cid) is None


async def test_caregiver_who_deleted_her_account_disappears_from_the_console(client, admin_headers):
    cid = await approved_caregiver(client, admin_headers)
    assert (await client.post(f"{BASE}/caregivers/{cid}/remove")).status_code == 403
    assert (await client.post(f"{BASE}/caregivers/{cid}/remove", headers=KEY)).status_code == 204
    assert (await client.get(f"{BASE}/caregivers/{cid}", headers=admin_headers)).status_code == 404
    assert (await client.post(f"{BASE}/caregivers/{cid}/remove", headers=KEY)).status_code == 204  # repetirlo no falla
