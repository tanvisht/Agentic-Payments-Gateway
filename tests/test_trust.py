import pytest
from fastapi.testclient import TestClient

from agentpay.core.crypto import KeyPair
from agentpay.trust.registry import AgentCard, Feedback
from tests.conftest import pay


@pytest.fixture
def merchant(admin):
    kp = KeyPair.generate()
    admin.post("/v1/registry/merchants", json={"merchant_id": "nike.com", "public_key": kp.public_b64})
    return kp


def fb(kp, pi_id, agent_id, score, merchant_id="nike.com"):
    f = Feedback(subject_agent_id=agent_id, merchant_id=merchant_id, payment_intent_id=pi_id, score=score, signature="")
    f.signature = kp.sign(f.payload())
    return f.model_dump()


def test_agent_card_must_be_signed_by_principal(admin, world):
    card = AgentCard(id=world["agent"].id, name="Shopper", agent_public_key=world["akp"].public_b64,
                     principal_id=world["principal"].id, capabilities=["acp", "x402"], signature="")
    bad = {**card.model_dump(), "signature": KeyPair.generate().sign(card.payload())}
    assert admin.post("/v1/registry/agents", json=bad).status_code == 401
    card.signature = world["pkp"].sign(card.payload())
    assert admin.post("/v1/registry/agents", json=card.model_dump()).status_code == 201
    pub = TestClient(admin.app).get(f"/v1/registry/agents/{world['agent'].id}").json()
    assert pub["card"]["name"] == "Shopper" and pub["reputation"]["count"] == 0


def test_feedback_requires_real_settled_payment(admin, world, merchant):
    aid = world["agent"].id
    assert admin.post("/v1/registry/feedback", json=fb(merchant, "pi_fake", aid, 10)).status_code == 422
    failed = pay(world, payment_method="pm_card_chargeDeclined")
    assert admin.post("/v1/registry/feedback", json=fb(merchant, failed["id"], aid, 10)).status_code == 422
    ok = pay(world)
    forged = fb(KeyPair.generate(), ok["id"], aid, 0)
    assert admin.post("/v1/registry/feedback", json=forged).status_code == 401
    assert admin.post("/v1/registry/feedback", json=fb(merchant, ok["id"], aid, 90)).status_code == 201
    assert admin.post("/v1/registry/feedback", json=fb(merchant, ok["id"], aid, 90)).status_code == 422  # one per payment


def test_wrong_merchant_cannot_review(admin, world):
    kp = KeyPair.generate()
    admin.post("/v1/registry/merchants", json={"merchant_id": "hoka.com", "public_key": kp.public_b64})
    pi = pay(world)  # paid to nike.com
    assert admin.post("/v1/registry/feedback", json=fb(kp, pi["id"], world["agent"].id, 0, "hoka.com")).status_code == 422


def test_bayesian_reputation_and_risk_integration(admin, world, merchant, gw):
    gw.risk.cfg.duplicate_window_s = 0
    aid = world["agent"].id
    pi = pay(world, amount=100)
    admin.post("/v1/registry/feedback", json=fb(merchant, pi["id"], aid, 0))
    assert gw.registry.reputation(aid)["score"] > 55  # one bad review can't sink a new agent
    for amt in (101, 102):
        pi = pay(world, amount=amt)
        assert pi["status"] == "succeeded"  # below min review count, no reputation penalty yet
        admin.post("/v1/registry/feedback", json=fb(merchant, pi["id"], aid, 0))
    rep = gw.registry.reputation(aid)
    assert rep["count"] == 3 and rep["raw_mean"] == 0 and 40 < rep["score"] < 45  # prior keeps it off the floor
    nxt = pay(world, amount=105)
    assert nxt["status"] == "requires_action" and any("reputation" in r for r in nxt["risk"]["reasons"])


def test_acp_merchant_rates_buyer_automatically(gw, app, world, admin):
    from agentpay.agent.acp_client import AcpClient
    m = app.state.merchant
    admin.post("/v1/registry/merchants", json={"merchant_id": "stride.run", "public_key": m.public_key})
    acp = AcpClient(TestClient(app))
    acp.connect(world["agent"].id)
    o = acp.call("commerce/offer", sessionId=acp.session_id, sku="strd-dry-tee", size="M")
    mand = gw.mandates.get(world["mandate"].id)
    mand.scope.merchants_allowed, mand.scope.categories_allowed = [], []
    gw.store.put("mandate", mand, k1=mand.agent_id)
    pi = pay(world, amount=o["price"], merchant_id="stride.run", category=None)
    acp.confirm(o["id"], pi["id"])
    assert gw.registry.reputation(world["agent"].id)["count"] == 1
