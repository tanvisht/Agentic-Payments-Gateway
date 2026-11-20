import secrets
import time

import pytest
from fastapi.testclient import TestClient

from agentpay.agent.client import GatewayHTTPError
from agentpay.core.crypto import KeyPair
from agentpay.mandates.service import mandate_signing_payload
from agentpay.core.payments import approval_payload
from tests.conftest import pay


def test_happy_path_and_ledger(world, gw):
    pi = pay(world)
    assert pi["status"] == "succeeded" and pi["amount_captured"] == 5000
    assert gw.ledger.balance("merchant:nike.com:receivable", "USD") == 5000
    assert gw.ledger.trial_balance() == {"USD": 0}
    m = world["client"].get_mandate(world["mandate"].id)
    assert m["spent_amount"] == 5000 and m["reserved_amount"] == 0


def test_manual_capture_partial_then_refund(world, gw):
    pi = pay(world, capture_method="manual")
    assert pi["status"] == "requires_capture"
    c = world["client"].request("POST", f"/v1/payment_intents/{pi['id']}/capture", {"amount": 3000})
    assert c["status"] == "succeeded" and c["amount_captured"] == 3000
    m = gw.mandates.get(world["mandate"].id)
    assert m.spent_amount == 3000 and m.reserved_amount == 0
    world["client"].refund(pi["id"], 1000)
    r2 = world["client"].refund(pi["id"])
    assert r2["amount"] == 2000
    final = world["client"].get_payment(pi["id"])
    assert final["status"] == "refunded" and final["amount_refunded"] == 3000
    assert gw.mandates.get(world["mandate"].id).spent_amount == 0
    assert gw.ledger.trial_balance() == {"USD": 0}
    with pytest.raises(GatewayHTTPError) as e:
        world["client"].refund(pi["id"], 1)
    assert e.value.status == 409


def test_cancel_authorized_releases_budget(world, gw):
    pi = pay(world, capture_method="manual")
    world["client"].request("POST", f"/v1/payment_intents/{pi['id']}/cancel")
    m = gw.mandates.get(world["mandate"].id)
    assert m.reserved_amount == 0 and m.txn_count == 0
    assert gw.ledger.trial_balance() == {"USD": 0}


def test_decline_releases_budget_and_retry(world, gw):
    pi = pay(world, payment_method="pm_card_chargeDeclinedInsufficientFunds")
    assert pi["status"] == "failed" and pi["last_error"]["decline_code"] == "insufficient_funds"
    assert gw.mandates.get(world["mandate"].id).reserved_amount == 0
    r = world["client"].request("POST", f"/v1/payment_intents/{pi['id']}/retry", {"payment_method": "pm_card_visa"})
    assert r["status"] == "succeeded"


def test_failover_to_backup_provider(world, gw):
    gw.router.providers["mock"].fail_all = True
    pi = pay(world)
    assert pi["status"] == "succeeded" and pi["provider"] == "mock_backup"
    assert [a["outcome"] for a in pi["attempts"]][:1] == ["retryable_error"]


def test_hard_decline_does_not_fail_over(world, gw):
    pay(world, payment_method="pm_card_chargeDeclined")
    assert all(c[0] != "authorize" for c in gw.router.providers["mock_backup"].calls)


def test_all_providers_down(world, gw):
    for p in gw.router.providers.values():
        p.fail_all = True
    pi = pay(world)
    assert pi["status"] == "failed" and pi["last_error"]["code"] == "no_provider_available"


def test_3ds_requires_action_then_sync(world, gw):
    pi = pay(world, payment_method="pm_card_authenticationRequired")
    assert pi["status"] == "requires_action" and pi["next_action"]["type"] == "redirect_to_url"
    gw.router.providers["mock"].complete_action(pi["provider_ref"], capture=True)
    s = world["client"].request("POST", f"/v1/payment_intents/{pi['id']}/sync")
    assert s["status"] == "succeeded"


def test_mandate_overspend_blocked(world):
    pay(world, amount=20000)
    pay(world, amount=20000)
    with pytest.raises(GatewayHTTPError) as e:
        pay(world, amount=20000)  # total 50000 limit
    assert e.value.status == 403 and e.value.body["error"]["details"]["rule"] == "budget"


def test_idempotent_replay_and_conflict(world, gw):
    a = pay(world, _idem="same-key")
    b = pay(world, _idem="same-key")
    assert a["id"] == b["id"]
    assert gw.mandates.get(world["mandate"].id).spent_amount == 5000
    with pytest.raises(GatewayHTTPError) as e:
        pay(world, _idem="same-key", amount=6000)
    assert e.value.status == 409


def test_signature_required_and_replay(world, app):
    raw = TestClient(app)
    r = raw.post("/v1/payment_intents", json={}, headers={"Authorization": f"Bearer {world['api_key']}"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_signature"
    # wrong key signs
    from agentpay.agent.demo import in_process_client
    evil = in_process_client(app, world["api_key"], KeyPair.generate())
    with pytest.raises(GatewayHTTPError) as e:
        pay({**world, "client": evil})
    assert e.value.status == 401
    # nonce replay
    c = world["client"]
    body = b'{"x":1}'
    h = c._headers("POST", "/v1/payment_intents", body, None)
    raw.post("/v1/payment_intents", content=body, headers=h)
    r2 = raw.post("/v1/payment_intents", content=body, headers=h)
    assert r2.json()["error"]["code"] == "replay_detected"


def test_stale_timestamp_rejected(world, app, monkeypatch):
    import types
    import agentpay.agent.client as cl
    monkeypatch.setattr(cl, "time", types.SimpleNamespace(time=lambda: 1_000_000))
    with pytest.raises(GatewayHTTPError) as e:
        pay(world)
    assert e.value.body["error"]["code"] == "invalid_signature"


def test_agent_isolation(world, gw, app):
    from agentpay.agent.demo import bootstrap, in_process_client
    _, _, _, akp2, key2, _ = bootstrap(gw)
    other = in_process_client(app, key2, akp2)
    pi = pay(world)
    with pytest.raises(GatewayHTTPError) as e:
        other.get_payment(pi["id"])
    assert e.value.status == 404
    with pytest.raises(GatewayHTTPError):
        other.get_mandate(world["mandate"].id)


def test_suspended_agent(world, admin):
    admin.post(f"/v1/agents/{world['agent'].id}/status", json={"status": "suspended"})
    with pytest.raises(GatewayHTTPError) as e:
        pay(world)
    assert e.value.status == 401


def test_human_approval_flow(world, gw, admin):
    m = gw.mandates.get(world["mandate"].id)
    m.scope.requires_human_above = 10000
    gw.store.put("mandate", m, k1=m.agent_id)
    pi = pay(world, amount=15000)
    assert pi["status"] == "requires_action" and pi["next_action"]["type"] == "principal_approval"
    bad = admin.post(f"/v1/payment_intents/{pi['id']}/approve", json={"signature": KeyPair.generate().sign({"x": 1})})
    assert bad.status_code == 401
    intent = gw.payments.get(pi["id"])
    ok = admin.post(f"/v1/payment_intents/{pi['id']}/approve", json={"signature": world["pkp"].sign(approval_payload(intent))})
    assert ok.json()["status"] == "succeeded"


def test_velocity_risk_decline(world, gw):
    gw.risk.cfg.velocity_max = 2
    pay(world, amount=100)
    pay(world, amount=101)
    with pytest.raises(GatewayHTTPError) as e:
        pay(world, amount=102)
    assert e.value.body["error"]["code"] == "risk_declined"


def test_admin_endpoints_require_admin(world, app):
    c = TestClient(app)
    assert c.get("/v1/events").status_code == 401
    c.headers["Authorization"] = f"Bearer {world['api_key']}"
    assert c.get("/v1/events").status_code == 401


def test_register_via_api(admin):
    pkp, akp = KeyPair.generate(), KeyPair.generate()
    p = admin.post("/v1/principals", json={"name": "P", "public_key": pkp.public_b64}).json()
    a = admin.post("/v1/agents", json={"principal_id": p["id"], "name": "A", "public_key": akp.public_b64}).json()
    assert a["api_key"].startswith("ak_") and "api_key_hash" not in a
    scope = {"currency": "USD", "max_amount_per_txn": 100, "max_total_amount": 200}
    nb, exp = int(time.time()), int(time.time()) + 60
    sig = pkp.sign(mandate_signing_payload(principal_id=p["id"], agent_id=a["id"], scope=scope, nonce="abcdefgh", not_before=nb, expires_at=exp))
    r = admin.post("/v1/mandates", json={"principal_id": p["id"], "agent_id": a["id"], "scope": scope, "nonce": "abcdefgh",
                                         "not_before": nb, "expires_at": exp, "signature": sig})
    assert r.status_code == 201
    assert admin.post("/v1/principals", json={"name": "P", "public_key": "nope"}).status_code == 422


def test_healthz(admin):
    assert admin.get("/healthz").json()["status"] == "ok"
