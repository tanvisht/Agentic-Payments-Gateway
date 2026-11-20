import json
import time
from types import SimpleNamespace

import httpx
import pytest
import stripe

from agentpay.core.errors import CardDeclined, NoProviderAvailable, ProviderError, ValidationFailed
from agentpay.ledger.ledger import Ledger, Posting
from agentpay.providers.base import AuthorizeRequest, ProviderStatus
from agentpay.providers.mock import MockProvider
from agentpay.providers.router import CircuitBreaker, ProviderRouter
from agentpay.providers.stripe_provider import StripeProvider
from agentpay.storage import Store
from agentpay.webhooks import signing
from agentpay.webhooks.dispatcher import EventBus


def req(pm="pm_card_visa", key="k1", capture=True):
    return AuthorizeRequest("pi_1", 1000, "USD", pm, capture, key)


def test_circuit_breaker_opens_and_half_opens():
    t = [0.0]
    br = CircuitBreaker(threshold=2, cooldown_s=10, clock=lambda: t[0])
    br.failure(); br.failure()
    assert br.state == "open" and not br.allow()
    t[0] = 11
    assert br.allow() and br.state == "half_open"
    br.success()
    assert br.state == "closed"


def test_router_skips_open_circuit():
    a, b = MockProvider("a", fail_all=True), MockProvider("b")
    r = ProviderRouter([a, b], breaker_threshold=1)
    r.authorize(req(key="x1"))
    p, _, attempts = r.authorize(req(key="x2"))
    assert p.name == "b" and attempts[0]["outcome"] == "skipped_circuit_open"


def test_router_unknown_preferred():
    with pytest.raises(NoProviderAvailable):
        ProviderRouter([MockProvider()]).authorize(req(), preferred="adyen")


def test_mock_idempotency():
    m = MockProvider()
    assert m.authorize(req(key="z")).provider_ref == m.authorize(req(key="z")).provider_ref


# -- Stripe adapter against a fake client ------------------------------
class FakePI:
    def __init__(self):
        self.calls = []
        self.raise_ = None

    def create(self, params, opts):
        self.calls.append((params, opts))
        if self.raise_:
            raise self.raise_
        status = "succeeded" if params["capture_method"] == "automatic" else "requires_capture"
        return {"id": "pi_stripe_1", "status": status, "amount": params["amount"], "amount_received": params["amount"] if status == "succeeded" else 0}

    def capture(self, ref, params, opts):
        return {"id": ref, "status": "succeeded", "amount": 1000, "amount_received": params["amount_to_capture"]}

    def cancel(self, ref, params, opts):
        return {"id": ref, "status": "canceled", "amount": 1000}

    def retrieve(self, ref):
        return {"id": ref, "status": "requires_action", "amount": 1000,
                "next_action": {"type": "redirect_to_url", "redirect_to_url": {"url": "https://hooks.stripe.com/x"}}}


class FakeRefunds:
    def create(self, params, opts):
        return {"id": "re_1", "status": "succeeded", "amount": params["amount"]}


@pytest.fixture
def stripe_p():
    pi = FakePI()
    client = SimpleNamespace(v1=SimpleNamespace(payment_intents=pi, refunds=FakeRefunds()))
    return StripeProvider(client=client, webhook_secret="whsec_test"), pi


def test_stripe_authorize_params(stripe_p):
    p, pi = stripe_p
    r = p.authorize(req(capture=False))
    params, opts = pi.calls[0]
    assert params["off_session"] and params["confirm"] and params["capture_method"] == "manual"
    assert params["currency"] == "usd" and opts["idempotency_key"] == "auth:k1"
    assert r.status is ProviderStatus.AUTHORIZED


def test_stripe_card_error_translation(stripe_p):
    p, pi = stripe_p
    pi.raise_ = stripe.CardError("declined", None, "card_declined")
    with pytest.raises(CardDeclined):
        p.authorize(req())
    pi.raise_ = stripe.APIConnectionError("net down")
    with pytest.raises(ProviderError) as e:
        p.authorize(req())
    assert e.value.retryable
    pi.raise_ = stripe.InvalidRequestError("bad pm", "payment_method")
    with pytest.raises(ValidationFailed):
        p.authorize(req())


def test_stripe_next_action_and_refund(stripe_p):
    p, _ = stripe_p
    r = p.retrieve("pi_x")
    assert r.status is ProviderStatus.REQUIRES_ACTION and r.next_action["url"].startswith("https://")
    assert p.refund("pi_x", 500, "k").amount == 500


def test_stripe_live_key_guard():
    with pytest.raises(ValueError):
        StripeProvider(secret_key="sk_live_abc", allow_live=False)


def test_stripe_webhook_signature(stripe_p):
    p, _ = stripe_p
    payload = json.dumps({"id": "evt_1", "object": "event", "type": "payment_intent.succeeded",
                          "data": {"object": {"id": "pi_abc", "object": "payment_intent"}}})
    ts = int(time.time())
    sig = stripe.WebhookSignature._compute_signature(f"{ts}.{payload}", "whsec_test")
    upd = p.parse_webhook(payload.encode(), f"t={ts},v1={sig}")
    assert upd.provider_ref == "pi_abc" and upd.status is ProviderStatus.CAPTURED
    from agentpay.core.errors import SignatureError
    with pytest.raises(SignatureError):
        p.parse_webhook(payload.encode(), f"t={ts},v1=deadbeef")


# -- ledger ---------------------------------------------------------------
def test_ledger_rejects_unbalanced():
    led = Ledger(Store())
    with pytest.raises(ValidationFailed):
        led.post([Posting("a", debit=1), Posting("b", credit=2)], currency="USD", ref="r")
    with pytest.raises(ValidationFailed):
        led.post([Posting("a", debit=1, credit=1), Posting("b")], currency="USD", ref="r")


def test_store_tx_rolls_back():
    st = Store()
    led = Ledger(st)
    with pytest.raises(RuntimeError):
        with st.tx():
            led.post([Posting("a", debit=5), Posting("b", credit=5)], currency="USD", ref="r")
            raise RuntimeError
    assert led.trial_balance() == {}


# -- outbound webhooks ----------------------------------------------------
def test_webhook_sign_verify():
    body = b'{"a":1}'
    h = signing.sign("sec", body, ts=1000)
    assert signing.verify("sec", body, h, now=1000)
    assert not signing.verify("sec", body, h, now=2000)
    assert not signing.verify("other", body, h, now=1000)
    assert not signing.verify("sec", body, "garbage", now=1000)


def test_webhook_delivery_retries():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(500 if len(seen) < 3 else 200)

    st = Store()
    bus = EventBus(st, transport=httpx.MockTransport(handler), async_delivery=False, sleep=lambda s: None)
    from agentpay.core.models import WebhookEndpoint
    st.put("webhook_endpoint", WebhookEndpoint(url="https://example.test/hook", secret="s"))
    bus.emit("payment_intent.succeeded", {"x": 1})
    assert len(seen) == 3 and bus.deliveries[-1]["ok"]
    assert signing.verify("s", seen[-1].content, seen[-1].headers[signing.HEADER])


# -- onboarding -------------------------------------------------------------
class FakeCustomers:
    def create(self, params, opts):
        return {"id": "cus_1"}


class FakeSetupIntents:
    def __init__(self):
        self.params = None

    def create(self, params, opts):
        self.params = params
        return {"id": "seti_1", "client_secret": "seti_1_secret_x"}

    def retrieve(self, ref):
        return {"id": ref, "status": "succeeded", "payment_method": "pm_123", "customer": "cus_1"}


def test_stripe_setup_intent_off_session():
    si = FakeSetupIntents()
    client = SimpleNamespace(v1=SimpleNamespace(customers=FakeCustomers(), setup_intents=si))
    p = StripeProvider(client=client)
    out = p.create_customer_setup("prn_1", "a@b.co", "k")
    assert out == {"customer": "cus_1", "setup_intent": "seti_1", "client_secret": "seti_1_secret_x"}
    assert si.params["usage"] == "off_session"
    assert p.attach_result("seti_1")["payment_method"] == "pm_123"
