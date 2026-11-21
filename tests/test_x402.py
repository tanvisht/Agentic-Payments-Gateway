import base64
import json
import secrets

import pytest

pytest.importorskip("eth_account")
from eth_account import Account  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from agentpay.x402.facilitator import LocalFacilitator  # noqa: E402
from agentpay.x402.paywall import build_router  # noqa: E402
from agentpay.x402.protocol import PaymentRequirements, encode_header, sign_payment  # noqa: E402

ASSET = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
MERCHANT = Account.create().address


@pytest.fixture
def env():
    f = LocalFacilitator()
    buyer = Account.create()
    f.fund(buyer.address, 1_000 * 10**6)
    app = FastAPI()
    app.include_router(build_router(f, asset=ASSET, network="base-sepolia", merchant_addresses={"stride.run": MERCHANT}))
    return f, buyer, TestClient(app)


def test_402_challenge_then_pay(env):
    f, buyer, c = env
    r = c.get("/x402/products/strd-dry-tee")
    assert r.status_code == 402
    req = PaymentRequirements(**r.json()["accepts"][0])
    assert req.payTo == MERCHANT and req.maxAmountRequired == str(3500 * 10**4)
    pay = sign_payment(buyer.key.hex(), req)
    ok = c.get("/x402/products/strd-dry-tee", headers={"X-PAYMENT": encode_header(pay)})
    assert ok.status_code == 200 and ok.json()["paid"]
    settled = json.loads(base64.b64decode(ok.headers["X-PAYMENT-RESPONSE"]))
    assert settled["success"] and settled["payer"] == buyer.address
    assert f.balances[MERCHANT.lower()] == 35_000_000
    replay = c.get("/x402/products/strd-dry-tee", headers={"X-PAYMENT": encode_header(pay)})
    assert replay.status_code == 402 and "nonce" in replay.json()["error"]


@pytest.mark.parametrize("mutate,reason", [
    (lambda p, req: p["payload"]["authorization"].update(value="1"), "amount"),
    (lambda p, req: p["payload"]["authorization"].update(to=Account.create().address), "payTo"),
    (lambda p, req: p.update(network="base"), "network"),
    (lambda p, req: p["payload"].update(signature=sign_payment(Account.create().key.hex(), req)["payload"]["signature"]), "signature"),
])
def test_tampered_payments_rejected(env, mutate, reason):
    f, buyer, _ = env
    req = PaymentRequirements(maxAmountRequired="1000", resource="r", payTo=MERCHANT, asset=ASSET)
    p = sign_payment(buyer.key.hex(), req)
    mutate(p, req)
    v = f.verify(p, req)
    assert not v.valid and reason in v.reason


def test_expired_and_unfunded(env):
    f, buyer, _ = env
    req = PaymentRequirements(maxAmountRequired="1000", resource="r", payTo=MERCHANT, asset=ASSET)
    old = sign_payment(buyer.key.hex(), req, now=1_000_000)
    assert "window" in f.verify(old, req).reason
    poor = Account.create()
    assert f.verify(sign_payment(poor.key.hex(), req), req).reason == "insufficient_funds"
    assert c_bad_header(env)


def c_bad_header(env):
    _, _, c = env
    r = c.get("/x402/products/strd-dry-tee", headers={"X-PAYMENT": "%%%"})
    return r.status_code == 402 and "malformed" in r.json()["error"]


def test_x402_provider_through_gateway_under_mandate():
    from agentpay.agent.demo import bootstrap, in_process_client
    from agentpay.api.app import create_app
    from agentpay.config import Settings
    from agentpay.gateway import Gateway
    from agentpay.providers.x402_provider import X402Provider

    f = LocalFacilitator()
    wallet = Account.create()  # agent's delegated stablecoin wallet
    f.fund(wallet.address, 200 * 10**6)
    prov = X402Provider(f, asset=ASSET, network="base-sepolia", merchant_addresses={"stride.run": MERCHANT})
    gw = Gateway.build(Settings(admin_key="x", async_webhooks=False, default_provider="x402"), providers=[prov])
    app = create_app(gw)
    _, _, _, akp, key, mandate = bootstrap(gw, merchants=["stride.run"])
    c = in_process_client(app, key, akp)

    def pay(amount, signed_amount=None, idem=None):
        req = prov.requirements(amount=signed_amount or amount, merchant_id="stride.run", resource="x")
        pm = "x402:" + encode_header(sign_payment(wallet.key.hex(), req))
        return c.create_payment(idempotency_key=idem or secrets.token_hex(6), mandate_id=mandate.id, amount=amount,
                                currency="USD", merchant_id="stride.run", category="running", payment_method=pm)

    ok = pay(12900)
    assert ok["status"] == "succeeded" and ok["provider"] == "x402"
    assert f.balances[MERCHANT.lower()] == 12900 * 10**4
    bad = pay(5000, signed_amount=4000)  # wallet signed a different amount than the intent
    assert bad["status"] == "failed" and bad["last_error"]["decline_code"] == "x402_invalid"
    assert gw.mandates.get(mandate.id).spent_amount == 12900
    assert gw.ledger.trial_balance() == {"USD": 0}
