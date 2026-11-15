"""Despliegue serverless (Vercel): simulador sin estado entre instancias, usuario de prueba, validación prod."""
import pytest

from app.billing.gateway import FakeGateway, set_gateway
from app.config import Settings
from tests.billing.billing_helpers import BILLING

pytestmark = pytest.mark.asyncio


def test_prod_refuses_integration_credentials():
    s = Settings(_env_file=None, environment="prod")
    with pytest.raises(RuntimeError, match="ADMIN_BILLING_GATEWAY"):
        s.validate_billing()
    ok = Settings(_env_file=None, environment="prod", billing_gateway="transbank", tbk_environment="production",
                  tbk_commerce_code="597012345678", billing_encryption_key="x" * 44)
    ok.validate_billing()
    assert ok.billing_mfa_required and not Settings(_env_file=None).billing_mfa_required


async def test_checkout_across_serverless_instances(client, app_headers):
    """Cada paso del checkout cae en una instancia distinta (simulador recién creado, sin memoria)."""
    h = app_headers()
    set_gateway(FakeGateway())
    data = (await client.post(f"{BILLING}/subscriptions", headers=h, json={"plan_code": "gold"})).json()
    token = data["inscription"]["token"]
    assert len(token) == 64

    set_gateway(FakeGateway())        # instancia 2: página del simulador
    assert (await client.post(f"{BILLING}/fake-webpay", data={"TBK_TOKEN": token})).status_code == 200
    set_gateway(FakeGateway())        # instancia 3: decisión
    r = await client.post(f"{BILLING}/fake-webpay/decision", data={"TBK_TOKEN": token, "decision": "approve_visa"})
    back = r.headers["location"].replace("http://localhost:8000", "")
    set_gateway(FakeGateway())        # instancia 4: retorno de Webpay + primer cobro
    r = await client.get(back)
    assert "result=success" in r.headers["location"], r.headers["location"]
    set_gateway(FakeGateway())        # instancia 5: consulta
    me = (await client.get(f"{BILLING}/me", headers=h)).json()
    assert me["entitlement"]["plan_code"] == "gold"
    set_gateway(None)


async def test_forged_simulator_token_is_rejected(client, fake_gw):
    forged = "fk" + "0" * 40 + "f" * 22
    assert (await client.post(f"{BILLING}/fake-webpay", data={"TBK_TOKEN": forged})).status_code == 404
    r = await client.get(f"{BILLING}/oneclick/return", params={"TBK_TOKEN": forged, "fake_decision": "approve_visa"})
    assert "result=error" in r.headers["location"]


async def test_dev_app_token_for_testers(client, fake_gw, admin_headers, analyst_headers, monkeypatch):
    from app.config import get_settings
    url = "/api/v1/admin/billing/dev/app-token"
    assert (await client.post(url, headers=analyst_headers)).status_code == 403
    r = await client.post(url, headers=admin_headers)
    assert r.status_code == 200
    tok = r.json()
    h = {"Authorization": f"Bearer {tok['token']}"}
    r = await client.post(f"{BILLING}/subscriptions", headers=h, json={"plan_code": "platinum"})
    assert r.status_code == 201 and r.json()["subscription"]["beneficiary_id"] == tok["family_id"]
    r = await client.post(f"{BILLING}/subscriptions", headers=h, json={"plan_code": "provider"})
    assert r.status_code == 201
    monkeypatch.setattr(get_settings(), "environment", "prod")
    monkeypatch.setattr(get_settings(), "billing_require_mfa", False)   # aislar la regla «no existe en prod»
    assert (await client.post(url, headers=admin_headers)).status_code == 404
