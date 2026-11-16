"""Helpers para recorrer el checkout con el simulador de Webpay."""
from urllib.parse import parse_qs, urlparse

BILLING = "/api/v1/billing"


async def complete_webpay(client, inscription: dict, decision: str = "approve_visa") -> dict:
    """Simula al usuario en Webpay y el retorno a response_url. Devuelve los parámetros del redirect final."""
    page = await client.post(inscription["url_webpay"].replace("http://localhost:8000", ""),
                             data={"TBK_TOKEN": inscription["token"]})
    assert page.status_code == 200, page.text
    r = await client.post(f"{BILLING}/fake-webpay/decision",
                          data={"TBK_TOKEN": inscription["token"], "decision": decision})
    assert r.status_code == 303
    back = urlparse(r.headers["location"])
    assert back.path == f"{BILLING}/oneclick/return"
    r = await client.get(f"{back.path}?{back.query}")
    assert r.status_code == 303, r.text
    final = urlparse(r.headers["location"])
    return {k: v[0] for k, v in parse_qs(final.query).items()}


async def subscribe_with_card(client, headers: dict, plan: str = "gold", decision: str = "approve_visa",
                              **body) -> tuple[dict, dict]:
    r = await client.post(f"{BILLING}/subscriptions", headers=headers, json={"plan_code": plan, **body})
    assert r.status_code == 201, r.text
    data = r.json()
    result = {}
    if data["next_action"] == "redirect_to_webpay":
        result = await complete_webpay(client, data["inscription"], decision)
    return data, result


async def make_past_due(client, fake_gw, app_headers, session) -> str:
    """Suscripción Dorado activa cuya renovación rechaza el emisor: queda en mora (past_due)."""
    import uuid
    from datetime import timedelta

    from app.billing.jobs import run_billing_cycle
    from app.billing.models import Subscription
    from app.billing.service import tbk_username
    from app.security import as_utc

    data, result = await subscribe_with_card(client, app_headers(), "gold")
    assert result.get("result", "success") == "success"
    sid = data["subscription"]["id"]
    sub = await session.get(Subscription, uuid.UUID(sid))
    for tbk in fake_gw.tbk_users.values():          # desde ahora el emisor rechaza (Mastercard demo)
        if tbk["username"] == tbk_username(sub.user_id):
            tbk["card"] = "mastercard"
    await run_billing_cycle(session, fake_gw, now=as_utc(sub.current_period_end) + timedelta(minutes=1))
    session.expire_all()
    assert (await session.get(Subscription, uuid.UUID(sid))).status == "past_due"
    return sid
