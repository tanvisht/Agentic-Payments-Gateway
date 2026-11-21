import pytest

from agentpay.agent.client import GatewayHTTPError
from agentpay.core.audit import price
from tests.conftest import pay


def test_audit_trail_captures_attempts_denials_and_outcomes(world, gw, admin):
    pay(world, amount=5000)
    pay(world, payment_method="pm_card_chargeDeclined", amount=4000)
    for bad in ({"merchant_id": "evil.shop"}, {"amount": 25000}):
        with pytest.raises(GatewayHTTPError):
            pay(world, **bad)
    pi = pay(world, amount=3000, capture_method="manual")
    world["client"].request("POST", f"/v1/payment_intents/{pi['id']}/capture", {})
    world["client"].refund(pi["id"], 1000)

    rep = world["client"].request("GET", f"/v1/mandates/{world['mandate'].id}/audit")
    assert rep["manifest"]["scope"]["max_total_amount"] == 50000
    s = rep["summary"]
    assert s["attempts"] == 5
    assert s["denied_by_rule"] == {"merchant": 1, "per_txn_limit": 1}
    assert s["payments"] == {"succeeded": 1, "failed": 1, "partially_refunded": 1}
    assert s["spent"] == 7000 and s["merchants"] == ["nike.com"]
    assert rep["compliance"]["all_within_scope"]
    kinds = [e["kind"] for e in rep["activity"]]
    assert kinds.count("mandate.violation") == 2 and "payment_intent.payment_failed" in kinds and "charge.refunded" in kinds


def test_audit_is_private_to_owning_agent(world, gw, app):
    from agentpay.agent.demo import bootstrap, in_process_client
    _, _, _, akp2, key2, _ = bootstrap(gw)
    with pytest.raises(GatewayHTTPError):
        in_process_client(app, key2, akp2).request("GET", f"/v1/mandates/{world['mandate'].id}/audit")


def test_llm_usage_cost_tracking(world, gw):
    c, mid = world["client"], world["mandate"].id
    c.request("POST", "/v1/agents/me/usage", {"model": "claude-opus-5-5", "input_tokens": 12000, "output_tokens": 800, "mandate_id": mid})
    c.request("POST", "/v1/agents/me/usage", {"model": "claude-haiku-5-5", "input_tokens": 100000, "output_tokens": 2000})
    s = c.request("GET", f"/v1/agents/{world['agent'].id}/usage")
    assert s["calls"] == 2
    assert s["by_model"]["claude-opus-5-5"]["cost_usd_micros"] == 12000 * 4 + 800 * 20  # $0.064
    assert s["cost_usd"] == pytest.approx(0.064 + 0.011)
    rep = c.request("GET", f"/v1/mandates/{mid}/audit")
    assert rep["llm_cost"]["calls"] == 1 and rep["llm_cost"]["cost_usd"] == pytest.approx(0.064)
    with pytest.raises(GatewayHTTPError):
        c.request("POST", "/v1/agents/me/usage", {"model": "x", "input_tokens": -1, "output_tokens": 0})


def test_price_table():
    assert price("claude-sonnet-5-5", 1_000_000, 1_000_000) == 12_000_000
