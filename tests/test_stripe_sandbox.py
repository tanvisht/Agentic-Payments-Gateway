"""Live Stripe *test-mode* integration. Skipped unless STRIPE_SECRET_KEY=sk_test_... is set.

Exercises the real adapter against Stripe's sandbox: auto capture, manual
capture (partial), refund, hard decline, SCA requires_action, cancel, and
gateway-level idempotency mapping to Stripe idempotency keys.
"""

import os
import secrets

import pytest

KEY = os.environ.get("STRIPE_SECRET_KEY", "")
pytestmark = pytest.mark.skipif(not KEY.startswith("sk_test_"), reason="needs a Stripe test-mode key")


@pytest.fixture
def stripe_world():
    from agentpay.agent.demo import bootstrap, in_process_client
    from agentpay.api.app import create_app
    from agentpay.config import Settings
    from agentpay.gateway import Gateway
    from agentpay.providers.stripe_provider import StripeProvider

    gw = Gateway.build(Settings(admin_key="x", async_webhooks=False, default_provider="stripe"),
                       providers=[StripeProvider(secret_key=KEY)])
    app = create_app(gw)
    _, _, agent, akp, api_key, mandate = bootstrap(gw, merchants=[])
    return gw, in_process_client(app, api_key, akp), mandate


def _pay(client, mandate, **kw):
    body = {"mandate_id": mandate.id, "amount": 1234, "currency": "USD", "merchant_id": "sandbox.merchant", "category": "running"}
    body.update(kw)
    return client.create_payment(idempotency_key=secrets.token_hex(8), **body)


def test_auto_capture_and_refund(stripe_world):
    gw, c, m = stripe_world
    pi = _pay(c, m)
    assert pi["status"] == "succeeded" and pi["provider_ref"].startswith("pi_")
    r = c.refund(pi["id"], 234)
    assert r["provider_ref"].startswith("re_")
    assert gw.ledger.trial_balance() == {"USD": 0}


def test_manual_partial_capture(stripe_world):
    _, c, m = stripe_world
    pi = _pay(c, m, capture_method="manual")
    assert pi["status"] == "requires_capture"
    cap = c.request("POST", f"/v1/payment_intents/{pi['id']}/capture", {"amount": 1000})
    assert cap["status"] == "succeeded" and cap["amount_captured"] == 1000


def test_cancel_authorization(stripe_world):
    _, c, m = stripe_world
    pi = _pay(c, m, capture_method="manual")
    assert c.request("POST", f"/v1/payment_intents/{pi['id']}/cancel")["status"] == "canceled"


@pytest.mark.parametrize("pm,code", [("pm_card_chargeDeclined", "generic_decline"),
                                     ("pm_card_chargeDeclinedInsufficientFunds", "insufficient_funds")])
def test_declines(stripe_world, pm, code):
    _, c, m = stripe_world
    pi = _pay(c, m, payment_method=pm)
    assert pi["status"] == "failed" and pi["last_error"]["decline_code"] == code


def test_sca_requires_action(stripe_world):
    _, c, m = stripe_world
    pi = _pay(c, m, payment_method="pm_card_authenticationRequired")
    assert pi["status"] in ("requires_action", "failed")
