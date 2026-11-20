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
