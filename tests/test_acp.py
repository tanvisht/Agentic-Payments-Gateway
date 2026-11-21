import secrets
import time

import pytest
from fastapi.testclient import TestClient

from agentpay.acp import requirements
from agentpay.acp.service import MerchantService, Offer
from agentpay.agent.client import GatewayHTTPError
from agentpay.core.models import MandateScope
from agentpay.mandates.service import mandate_signing_payload
from tests.conftest import pay


@pytest.fixture
def rpc(app):
    c = TestClient(app)
    n = iter(range(10**6))

    def call(method, **params):
        r = c.post("/acp/rpc", json={"jsonrpc": "2.0", "id": next(n), "method": method, "params": params}).json()
        return r
    call.raw = c
    return call


@pytest.fixture
def open_mandate(gw, world):
    scope = MandateScope(currency="USD", max_amount_per_txn=30000, max_total_amount=100000)
    nb, exp, nonce = int(time.time()) - 1, int(time.time()) + 600, secrets.token_hex(8)
    sig = world["pkp"].sign(mandate_signing_payload(principal_id=world["principal"].id, agent_id=world["agent"].id,
                                                    scope=scope, nonce=nonce, not_before=nb, expires_at=exp))
    return gw.mandates.register(principal=world["principal"], agent=world["agent"], scope=scope, nonce=nonce,
                                not_before=nb, expires_at=exp, signature=sig)


@pytest.mark.parametrize("text,expect", [
    ("running shoes under $150 size 10", {"category": "running", "max_price": 15000, "size": "10"}),
    ("a hotel in soho, budget $300", {"category": "lodging", "max_price": 30000}),
    ("trail shoes", {"category": "trail"}),
    ("size m tee up to $40", {"category": "apparel", "max_price": 4000, "size": "M"}),
])
def test_requirement_extraction(text, expect):
    assert requirements.extract(text) == expect


def test_full_acp_purchase(rpc, world, open_mandate, app, gw):
    init = rpc("initialize")["result"]
    assert "commerce/pay" in init["methods"]
    sid = rpc("session/new", buyerId=world["agent"].id)["result"]["sessionId"]

    r1 = rpc("session/prompt", sessionId=sid, prompt=[{"type": "text", "text": "I need running shoes"}])["result"]
    assert r1["type"] == "clarification" and set(r1["missing"]) == {"max_price", "size"}
    r2 = rpc("session/prompt", sessionId=sid, text="under $160, size 10")["result"]
    assert r2["type"] == "offers" and r2["offers"]
    assert all(o["price"] <= 16000 and o["category"] == "running" for o in r2["offers"])
    offer = r2["offers"][0]
    assert MerchantService.verify_offer(offer, init["merchantKey"])
    tampered = {**offer, "price": 1}
    assert not MerchantService.verify_offer(tampered, init["merchantKey"])

    pi = pay(world, mandate_id=open_mandate.id, amount=offer["price"], merchant_id=offer["merchant_id"], category=None)
    order = rpc("commerce/pay", sessionId=sid, offerId=offer["id"], paymentIntentId=pi["id"])["result"]
    assert order["order"]["amount"] == offer["price"] and not order["replayed"]
    again = rpc("commerce/pay", sessionId=sid, offerId=offer["id"], paymentIntentId=pi["id"])["result"]
    assert again["replayed"] and again["order"]["id"] == order["order"]["id"]
    assert rpc("order/get", orderId=order["order"]["id"])["result"]["status"] == "confirmed"


def test_acp_payment_must_match_offer(rpc, world, open_mandate):
    sid = rpc("session/new", buyerId="b")["result"]["sessionId"]
    offer = rpc("commerce/offer", sessionId=sid, sku="strd-aero-2", size="10")["result"]
    cheap = pay(world, mandate_id=open_mandate.id, amount=100, merchant_id=offer["merchant_id"], category=None)
    err = rpc("commerce/pay", sessionId=sid, offerId=offer["id"], paymentIntentId=cheap["id"])["error"]
    assert err["data"]["code"] == "acp_error" and "amount" in err["message"]
    wrong_merchant = pay(world, mandate_id=open_mandate.id, amount=offer["price"], merchant_id="evil.shop", category=None)
    err = rpc("commerce/pay", sessionId=sid, offerId=offer["id"], paymentIntentId=wrong_merchant["id"])["error"]
    assert "different merchant" in err["message"]
    failed = pay(world, mandate_id=open_mandate.id, amount=offer["price"], merchant_id=offer["merchant_id"],
                 category=None, payment_method="pm_card_chargeDeclined")
    err = rpc("commerce/pay", sessionId=sid, offerId=offer["id"], paymentIntentId=failed["id"])["error"]
    assert "status is failed" in err["message"]


def test_one_payment_cannot_buy_two_orders(rpc, world, open_mandate):
    sid = rpc("session/new", buyerId="b")["result"]["sessionId"]
    o1 = rpc("commerce/offer", sessionId=sid, sku="strd-dry-tee", size="M")["result"]
    o2 = rpc("commerce/offer", sessionId=sid, sku="strd-dry-tee", size="L")["result"]
    pi = pay(world, mandate_id=open_mandate.id, amount=o1["price"], merchant_id=o1["merchant_id"], category=None)
    assert "order" in rpc("commerce/pay", sessionId=sid, offerId=o1["id"], paymentIntentId=pi["id"])["result"]
    err = rpc("commerce/pay", sessionId=sid, offerId=o2["id"], paymentIntentId=pi["id"])["error"]
    assert "already used" in err["message"]


def test_expired_offer(rpc, gw, world, open_mandate):
    sid = rpc("session/new", buyerId="b")["result"]["sessionId"]
    o = rpc("commerce/offer", sessionId=sid, sku="strd-socks-3")["result"]
    stored = gw.store.get("acp_offer", o["id"], Offer)
    stored.expires_at = int(time.time()) - 1
    gw.store.put("acp_offer", stored, k1=sid)
    pi = pay(world, mandate_id=open_mandate.id, amount=o["price"], merchant_id=o["merchant_id"], category=None)
    assert "expired" in rpc("commerce/pay", sessionId=sid, offerId=o["id"], paymentIntentId=pi["id"])["error"]["message"]


def test_closed_session_and_unknown_session(rpc):
    sid = rpc("session/new", buyerId="b")["result"]["sessionId"]
    rpc("session/close", sessionId=sid)
    assert rpc("session/prompt", sessionId=sid, text="hi")["error"]["data"]["code"] == "acp_error"
    assert rpc("session/load", sessionId="sess_nope")["error"]["code"] == -32004


def test_jsonrpc_protocol_errors(rpc):
    c = rpc.raw
    assert c.post("/acp/rpc", content=b"{not json").json()["error"]["code"] == -32700
    assert c.post("/acp/rpc", json={"id": 1}).json()["error"]["code"] == -32600
    assert rpc("nope/method")["error"]["code"] == -32601
    assert c.post("/acp/rpc", json={"jsonrpc": "2.0", "id": 1, "method": "session/new", "params": [1]}).json()["error"]["code"] == -32602
    batch = c.post("/acp/rpc", json=[{"jsonrpc": "2.0", "id": 1, "method": "initialize"},
                                     {"jsonrpc": "2.0", "method": "initialize"}]).json()
    assert len(batch) == 1 and batch[0]["id"] == 1  # notifications get no response
    assert c.post("/acp/rpc", json=[]).json()["error"]["code"] == -32600


def test_no_matching_products(rpc):
    sid = rpc("session/new", buyerId="b")["result"]["sessionId"]
    r = rpc("session/prompt", sessionId=sid, text="running shoes under $5 size 10")["result"]
    assert r["type"] == "offers" and r["offers"] == []


@pytest.mark.parametrize("text,cat,skus", [
    ("noise cancelling headphones under $400", "electronics", {"sa-anc-h900"}),
    ("a usb-c charger under $50", "accessories", {"vg-usbc-65w"}),
    ("wireless earbuds, budget $250", "electronics", {"sa-buds-pro"}),
])
def test_electronics_catalog(rpc, text, cat, skus):
    sid = rpc("session/new", buyerId="b")["result"]["sessionId"]
    r = rpc("session/prompt", sessionId=sid, text=text)["result"]
    assert r["type"] == "offers" and r["requirements"]["category"] == cat  # no size needed for electronics
    assert skus <= {o["sku"] for o in r["offers"]}
