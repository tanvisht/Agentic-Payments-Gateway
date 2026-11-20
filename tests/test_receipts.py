from agentpay.core import receipts
from tests.conftest import pay


def test_signed_receipt_verifies_and_detects_tampering(world, admin):
    pi = pay(world)
    world["client"].refund(pi["id"], 1000)
    rec = world["client"].request("GET", f"/v1/payment_intents/{pi['id']}/receipt")
    key = admin.get("/.well-known/agentpay").json()["gateway_key"]
    assert rec["gateway_key"] == key and receipts.verify(rec, key)
    assert rec["payment_intent"]["amount_refunded"] == 1000
    assert {e["memo"] for e in rec["ledger"]} == {"capture", "refund"}
    forged = {**rec, "payment_intent": {**rec["payment_intent"], "amount_captured": 1}}
    assert not receipts.verify(forged, key)


def test_receipt_pdf(world, admin):
    pi = pay(world)
    r = admin.get(f"/v1/payment_intents/{pi['id']}/receipt?format=pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf" and r.content[:4] == b"%PDF"


def test_receipt_isolation(world, gw, app):
    from agentpay.agent.client import GatewayHTTPError
    from agentpay.agent.demo import bootstrap, in_process_client
    import pytest
    pi = pay(world)
    _, _, _, akp2, key2, _ = bootstrap(gw)
    with pytest.raises(GatewayHTTPError):
        in_process_client(app, key2, akp2).request("GET", f"/v1/payment_intents/{pi['id']}/receipt")
