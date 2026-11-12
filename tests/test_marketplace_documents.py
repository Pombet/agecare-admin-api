"""Documentos de cuidadoras y sincronización de perfiles desde la app.

Necesitan PostgreSQL real (esquema admin, RLS, historial): ver TEST_DATABASE_URL en tests/conftest.py.
"""
import os
from uuid import uuid4

import pytest
from sqlalchemy import text

import app.database as database
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


def profile(cid=None, **kw):
    return {"caregiver_id": str(cid or uuid4()), "name": "Pedro Salinas", "email": f"p{uuid4().hex[:8]}@cuidado.cl",
            "zone": "Providencia", "specialties": ["Alzheimer"], "languages": ["Español"],
            "certifications_count": 0, **kw}


async def sync(client, **kw):
    r = await client.post(f"{BASE}/caregivers/sync", headers=KEY, json=profile(**kw))
    assert r.status_code == 201, r.text
    return r.json()["caregiver_id"]


async def upload(client, cid, doc_type, name="doc.png", is_change=False):
    r = await client.post(f"{BASE}/caregivers/documents/sync", headers=KEY, json={
        "caregiver_id": cid, "doc_type": doc_type, "file_url": f"https://files.example/{uuid4().hex}/{name}",
        "original_filename": name, "is_change": is_change})
    assert r.status_code == 201, r.text
    return r.json()


async def review(client, headers, cid, doc_type, action, note=None):
    return await client.post(f"{BASE}/caregivers/{cid}/documents/{doc_type}/review", headers=headers,
                             json={"action": action, "note": note})


async def detail(client, headers, cid):
    r = await client.get(f"{BASE}/caregivers/{cid}", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def test_app_sync_needs_the_shared_key(client, monkeypatch):
    body = profile()
    assert (await client.post(f"{BASE}/caregivers/sync", json=body)).status_code == 403
    assert (await client.post(f"{BASE}/caregivers/sync", json=body, headers={"X-Internal-Key": "otra"})).status_code == 403
    monkeypatch.setattr(get_settings(), "internal_sync_key", "")
    r = await client.post(f"{BASE}/caregivers/sync", json=body, headers=KEY)
    assert r.status_code == 503 and r.json()["error"]["code"] == "SYNC_DISABLED"


async def test_profile_arrives_pending_and_resync_keeps_the_staff_decision(client, admin_headers):
    cid = uuid4()
    await sync(client, cid=cid)
    d = await detail(client, admin_headers, cid)
    assert d["status"] == "pending" and d["zone"] == "Providencia" and d["documents"] == []
    again = profile(cid=cid, zone="Ñuñoa", photo_url="https://files.example/foto.png")
    assert (await client.post(f"{BASE}/caregivers/sync", headers=KEY, json=again)).status_code == 201
    d = await detail(client, admin_headers, cid)
    assert d["zone"] == "Ñuñoa" and d["photo_url"] == "https://files.example/foto.png" and d["status"] == "pending"


async def test_document_of_an_unknown_caregiver_or_with_a_bad_type_is_refused(client):
    r = await client.post(f"{BASE}/caregivers/documents/sync", headers=KEY, json={
        "caregiver_id": str(uuid4()), "doc_type": "id_card", "file_url": "https://f.example/a.png", "original_filename": "a.png"})
    assert r.status_code == 404
    cid = await sync(client)
    for bad in ({"doc_type": "pasaporte"}, {"file_url": "javascript:alert(1)"}, {"original_filename": ""}):
        body = {"caregiver_id": cid, "doc_type": "id_card", "file_url": "https://f.example/a.png",
                "original_filename": "a.png", **bad}
        assert (await client.post(f"{BASE}/caregivers/documents/sync", headers=KEY, json=body)).status_code == 422


async def test_profile_cannot_be_approved_until_the_required_documents_are_approved(client, admin_headers):
    cid = await sync(client)
    await upload(client, cid, "id_card")
    await upload(client, cid, "background_check")
    d = await detail(client, admin_headers, cid)
    assert d["certifications_verified"] is False and d["pending_documents"] == 2

    approve = lambda: client.patch(f"{BASE}/caregivers/{cid}", headers=admin_headers, json={"status": "approved"})
    assert (await approve()).status_code == 409
    assert (await review(client, admin_headers, cid, "id_card", "approve")).status_code == 200
    r = await approve()
    assert r.status_code == 409 and r.json()["error"]["code"] == "CERTIFICATION_REQUIRED"  # faltan los antecedentes
    assert (await review(client, admin_headers, cid, "background_check", "approve")).status_code == 200
    d = await detail(client, admin_headers, cid)
    assert d["certifications_verified"] is True and d["pending_documents"] == 0 and d["certifications_count"] >= 2
    r = await approve()
    assert r.status_code == 200 and r.json()["status"] == "approved"


async def test_rejecting_needs_a_reason_and_a_reviewed_document_cannot_be_reviewed_again(client, admin_headers):
    cid = await sync(client)
    await upload(client, cid, "id_card")
    r = await review(client, admin_headers, cid, "id_card", "reject")
    assert r.status_code == 422 and r.json()["error"]["code"] == "NOTE_REQUIRED"
    r = await review(client, admin_headers, cid, "id_card", "reject", "La imagen no es legible")
    assert r.status_code == 200 and r.json()["status"] == "rejected" and r.json()["review_note"] == "La imagen no es legible"
    assert r.json()["reviewed_by_name"]
    r = await review(client, admin_headers, cid, "id_card", "approve")
    assert r.status_code == 409 and r.json()["error"]["code"] == "NOTHING_TO_REVIEW"
    # La cuidadora corrige el archivo y vuelve a quedar pendiente.
    assert (await upload(client, cid, "id_card", "nuevo.png"))["status"] == "pending"
    assert (await review(client, admin_headers, cid, "id_card", "approve")).status_code == 200
    assert (await review(client, admin_headers, cid, "certificate", "approve")).status_code == 404  # nunca subió ese


async def test_changing_an_approved_document_needs_a_new_approval(client, admin_headers):
    cid = await sync(client)
    first = await upload(client, cid, "id_card", "vigente.png")
    await review(client, admin_headers, cid, "id_card", "approve")
    changed = await upload(client, cid, "id_card", "nuevo.png", is_change=True)
    assert changed["status"] == "approved" and changed["file_url"] == first["file_url"]  # el vigente sigue
    assert changed["pending_filename"] == "nuevo.png"
    assert (await detail(client, admin_headers, cid))["pending_documents"] == 1
    # Rechazar el cambio deja el documento aprobado.
    r = await review(client, admin_headers, cid, "id_card", "reject", "No corresponde al titular")
    assert r.json()["status"] == "approved" and r.json()["pending_filename"] is None
    # Un segundo cambio, esta vez aprobado, pasa a ser el vigente.
    again = await upload(client, cid, "id_card", "otro.png", is_change=True)
    r = await review(client, admin_headers, cid, "id_card", "approve")
    assert r.json()["original_filename"] == "otro.png" and r.json()["file_url"] == again["pending_file_url"]


async def test_app_can_read_back_the_decisions_and_the_statuses(client, admin_headers):
    cid = await sync(client)
    await upload(client, cid, "id_card")
    await review(client, admin_headers, cid, "id_card", "approve")
    r = await client.get(f"{BASE}/caregivers/documents", params={"caregiver_id": cid}, headers=KEY)
    assert r.status_code == 200 and r.json()["items"][0]["status"] == "approved"
    assert (await client.get(f"{BASE}/caregivers/documents", params={"caregiver_id": cid})).status_code == 403
    r = await client.get(f"{BASE}/caregivers/statuses", headers=KEY)
    assert {"caregiver_id": cid, "status": "pending"} in r.json()["items"]


async def test_only_staff_with_marketplace_write_can_review(client, admin_headers, analyst_headers):
    cid = await sync(client)
    await upload(client, cid, "id_card")
    assert (await client.get(f"{BASE}/caregivers/{cid}")).status_code == 401
    assert (await review(client, analyst_headers, cid, "id_card", "approve")).status_code == 403
    assert (await review(client, admin_headers, cid, "id_card", "approve")).status_code == 200


async def _scalar(sql, tenant=None, **params):
    """Consulta como el rol de la API (agecare_admin_api): el usuario de la base de pruebas es superusuario y se
    salta la seguridad por filas, el rol real no."""
    async with database.get_engine().connect() as conn:
        async with conn.begin():
            await conn.execute(text("SET LOCAL ROLE agecare_admin_api"))
            if tenant:
                await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant})
            return (await conn.execute(text(sql), params)).scalar_one()


async def test_every_change_is_kept_in_the_history(client, admin_headers):
    cid = await sync(client)
    await upload(client, cid, "id_card")
    await review(client, admin_headers, cid, "id_card", "approve")
    tenant = str(await _scalar("SELECT id FROM admin.tenants WHERE code = 'local'"))
    ops = await _scalar("SELECT string_agg(op, '' ORDER BY history_id) FROM admin.marketplace_caregiver_documents_history "
                        "WHERE caregiver_id = :c", tenant=tenant, c=cid)
    assert ops == "IU"  # creado y aprobado
    changed_by = await _scalar("SELECT changed_by FROM admin.marketplace_caregiver_documents_history "
                               "WHERE caregiver_id = :c AND op = 'U'", tenant=tenant, c=cid)
    assert changed_by is not None  # queda quién lo cambió


async def test_documents_are_isolated_by_tenant(client):
    cid = await sync(client)
    await upload(client, cid, "id_card")
    tenant = str(await _scalar("SELECT id FROM admin.tenants WHERE code = 'local'"))
    sql = "SELECT count(*) FROM admin.marketplace_caregiver_documents WHERE caregiver_id = :c"
    assert await _scalar(sql, tenant=tenant, c=cid) == 1
    assert await _scalar(sql, tenant=str(uuid4()), c=cid) == 0  # otra organización no los ve
