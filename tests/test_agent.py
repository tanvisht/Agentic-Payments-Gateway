from agentpay.agent.runner import run_deterministic
from agentpay.agent.tools import ToolExecutor


def test_deterministic_agent_buys_within_mandate(world, gw, monkeypatch):
    events = []
    out = run_deterministic("Buy running shoes under $150", ToolExecutor(world["client"], world["mandate"].id),
                            on_event=lambda k, d: events.append(k))
    assert "succeeded" in out
    assert gw.mandates.get(world["mandate"].id).spent_amount <= 15000


def test_agent_tool_error_is_reported(world):
    tools = ToolExecutor(world["client"], world["mandate"].id)
    out, err = tools("purchase", {"sku": "ua-flight-boarding"})  # travel not allowed by mandate
    assert err and "mandate_violation" in out


def test_agent_negotiates_via_acp_and_gets_order(world, gw, app):
    from fastapi.testclient import TestClient

    from agentpay.acp.service import Order
    from agentpay.agent.acp_client import AcpClient
    from agentpay.agent.runner import run_acp_deterministic

    m = gw.mandates.get(world["mandate"].id)
    m.scope.merchants_allowed = ["stride.run"]
    m.scope.categories_allowed = []
    gw.store.put("mandate", m, k1=m.agent_id)
    acp = AcpClient(TestClient(app))
    acp.connect(world["agent"].id)
    tools = ToolExecutor(world["client"], m.id, acp=acp)
    out = run_acp_deterministic("running shoes under $170", tools, answers=["size 10"])
    assert "confirmed" in out
    orders = gw.store.list("acp_order", Order)
    assert len(orders) == 1 and orders[0].merchant_id == "stride.run" and orders[0].amount <= 17000


def test_agent_refuses_forged_offer(world, app):
    from fastapi.testclient import TestClient

    from agentpay.agent.acp_client import AcpClient

    acp = AcpClient(TestClient(app))
    acp.connect("b")
    acp.merchant_key = __import__("agentpay.core.crypto", fromlist=["KeyPair"]).KeyPair.generate().public_b64  # MITM key
    tools = ToolExecutor(world["client"], world["mandate"].id, acp=acp)
    res = tools("ask_merchant", {"message": "running shoes under $200 size 10"})[0]
    assert '"signature_valid": false' in res
    out, err = tools("buy_offer", {"offer_id": __import__("json").loads(res)["offers"][0]["id"]})
    assert err and "unverified" in out
