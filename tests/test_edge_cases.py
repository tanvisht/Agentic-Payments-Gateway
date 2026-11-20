import secrets
import threading
import time

import pytest

from agentpay.agent.client import GatewayHTTPError
from agentpay.agent.demo import in_process_client
from agentpay.core.models import IntentStatus, MandateScope
from agentpay.mandates.service import mandate_signing_payload
from agentpay.providers.base import ProviderStatus, WebhookUpdate
from tests.conftest import pay


def test_concurrent_agents_cannot_overspend(world, gw):
    """20 threads race $50 payments against a $500 mandate: exactly 10 may succeed."""
    gw.risk.cfg.velocity_max = 1000
    gw.risk.cfg.duplicate_window_s = 0
    results, errors = [], []

    def worker():
        try:
            results.append(pay(world, amount=5000))
        except GatewayHTTPError as e:
            errors.append(e.body["error"]["code"])

    ts = [threading.Thread(target=worker) for _ in range(20)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    ok = [r for r in results if r["status"] == "succeeded"]
    assert len(ok) == 10
    assert set(errors) == {"mandate_violation"}
    m = gw.mandates.get(world["mandate"].id)
    assert m.spent_amount == 50000 and m.reserved_amount == 0 and m.status == "exhausted"
    assert gw.ledger.trial_balance() == {"USD": 0}


def test_concurrent_same_idempotency_key_charges_once(world, gw):
    out = []

    def worker():
        try:
            out.append(pay(world, _idem="race-key")["id"])
        except GatewayHTTPError as e:
            out.append(e.body["error"]["code"])

    ts = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    ids = {o for o in out if o.startswith("pi_")}
    assert len(ids) == 1
    assert gw.mandates.get(world["mandate"].id).spent_amount == 5000


def test_webhook_duplicate_and_out_of_order(world, gw):
    pi = pay(world, payment_method="pm_card_authenticationRequired")
    ref = pi["provider_ref"]
    gw.router.providers["mock"].complete_action(ref, capture=True)
    # succeeded arrives, then a stale requires_action, then succeeded again
    gw.payments.apply_webhook(WebhookUpdate(ref, ProviderStatus.CAPTURED, "payment_intent.succeeded"))
    gw.payments.apply_webhook(WebhookUpdate(ref, ProviderStatus.REQUIRES_ACTION, "payment_intent.requires_action"))
    gw.payments.apply_webhook(WebhookUpdate(ref, ProviderStatus.CAPTURED, "payment_intent.succeeded"))
    final = gw.payments.get(pi["id"])
    assert final.status is IntentStatus.SUCCEEDED
    assert len([e for e in gw.ledger.entries(pi["id"]) if e["memo"] == "capture"]) == 2  # one balanced journal = 2 rows
    assert gw.mandates.get(world["mandate"].id).spent_amount == 5000


def test_webhook_for_unknown_ref_is_ignored(gw):
    assert gw.payments.apply_webhook(WebhookUpdate("pi_unknown", ProviderStatus.CAPTURED, "x")) is None


def _mandate(gw, world, currency, per_txn, total):
    scope = MandateScope(currency=currency, max_amount_per_txn=per_txn, max_total_amount=total)
    nb, exp, nonce = int(time.time()) - 1, int(time.time()) + 600, secrets.token_hex(8)
    sig = world["pkp"].sign(mandate_signing_payload(principal_id=world["principal"].id, agent_id=world["agent"].id,
                                                    scope=scope, nonce=nonce, not_before=nb, expires_at=exp))
    return gw.mandates.register(principal=world["principal"], agent=world["agent"], scope=scope, nonce=nonce,
                                not_before=nb, expires_at=exp, signature=sig)


@pytest.mark.parametrize("cur,amount", [("JPY", 15000), ("KWD", 12345), ("EUR", 999)])
def test_non_two_decimal_currencies_end_to_end(world, gw, cur, amount):
    m = _mandate(gw, world, cur, amount, amount * 2)
    pi = pay(world, mandate_id=m.id, currency=cur.lower(), amount=amount, category=None, merchant_id="any.shop")
    assert pi["status"] == "succeeded" and pi["currency"] == cur and pi["amount_captured"] == amount
    world["client"].refund(pi["id"], amount // 3)
    assert gw.ledger.trial_balance()[cur] == 0


def test_unsupported_currency_rejected(world):
    with pytest.raises(GatewayHTTPError) as e:
        pay(world, currency="XYZ")
    assert e.value.status in (403, 422)


@pytest.mark.parametrize("amount", [0, -5, 10**13])
def test_invalid_amounts_rejected(world, amount):
    with pytest.raises(GatewayHTTPError) as e:
        pay(world, amount=amount)
    assert e.value.status == 422


def test_capture_more_than_authorized_rejected(world):
    pi = pay(world, capture_method="manual")
    with pytest.raises(GatewayHTTPError) as e:
        world["client"].request("POST", f"/v1/payment_intents/{pi['id']}/capture", {"amount": 5001})
    assert e.value.status == 422
    with pytest.raises(GatewayHTTPError) as e:
        world["client"].refund(pi["id"])  # not captured yet
    assert e.value.status == 409


def test_double_capture_and_cancel_after_capture(world):
    pi = pay(world, capture_method="manual")
    world["client"].request("POST", f"/v1/payment_intents/{pi['id']}/capture", {})
    for path in ("capture", "cancel"):
        with pytest.raises(GatewayHTTPError) as e:
            world["client"].request("POST", f"/v1/payment_intents/{pi['id']}/{path}", {})
        assert e.value.status == 409


def test_revoked_mandate_blocks_retry_and_approval(world, gw, admin):
    pi = pay(world, payment_method="pm_card_chargeDeclined")
    admin.post(f"/v1/mandates/{world['mandate'].id}/revoke")
    with pytest.raises(GatewayHTTPError) as e:
        world["client"].request("POST", f"/v1/payment_intents/{pi['id']}/retry", {"payment_method": "pm_card_visa"})
    assert e.value.body["error"]["details"]["rule"] == "inactive"


def test_mandate_count_limit(world, gw):
    m = _mandate(gw, world, "USD", 100, 10000)
    m.scope.max_txn_count = 2
    gw.store.put("mandate", m, k1=m.agent_id)
    for a in (10, 11):
        pay(world, mandate_id=m.id, amount=a, category=None)
    with pytest.raises(GatewayHTTPError) as e:
        pay(world, mandate_id=m.id, amount=12, category=None)
    assert e.value.body["error"]["details"]["rule"] == "txn_count"


def test_failed_payment_does_not_consume_txn_count(world, gw):
    m = _mandate(gw, world, "USD", 100, 10000)
    m.scope.max_txn_count = 1
    gw.store.put("mandate", m, k1=m.agent_id)
    pay(world, mandate_id=m.id, amount=10, category=None, payment_method="pm_card_chargeDeclined")
    assert pay(world, mandate_id=m.id, amount=10, category=None)["status"] == "succeeded"


def test_malformed_body_after_valid_signature(world, app):
    with pytest.raises(GatewayHTTPError) as e:
        world["client"].request("POST", "/v1/payment_intents", {"amount": "lots"})
    assert e.value.status == 422 and e.value.body["error"]["code"] == "validation_failed"
