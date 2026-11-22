import io
import json

import pytest

from agentpay.agent.trace import MAX_GOAL, run_trace


def test_trace_is_a_complete_real_purchase():
    t = run_trace("Buy noise cancelling headphones under $400", budget_cents=50000)
    kinds = [e["kind"] for e in t["events"]]
    for k in ("mandate.signed", "payment_intent.created", "payment_intent.succeeded", "acp.order.confirmed", "ledger", "receipt"):
        assert k in kinds
    assert "confirmed" in t["result"]
    ledger = next(e for e in t["events"] if e["kind"] == "ledger")["data"]
    assert ledger["trial_balance"] == {"USD": 0}


def test_budget_is_enforced_by_the_mandate():
    t = run_trace("Buy a laptop", budget_cents=30000)  # laptop costs $1,199
    assert "No verified offer" in t["result"]
    assert "payment_intent.succeeded" not in [e["kind"] for e in t["events"]]


@pytest.mark.parametrize("goal", ["", "x" * 5000, "<script>alert(1)</script> running shoes under $150"])
def test_hostile_or_odd_goals_are_bounded(goal):
    t = run_trace(goal, budget_cents=10**9)
    assert len(t["goal"]) <= MAX_GOAL
    assert t["events"]


def _call(method, body=None, raw=None):
    import api.run as fn

    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else b"")
    h = object.__new__(fn.handler)
    h.headers = {"content-length": str(len(data))}
    h.rfile, h.wfile = io.BytesIO(data), io.BytesIO()
    sent = {}
    h.send_response = lambda s: sent.setdefault("status", s)
    h.send_header = lambda *a: None
    h.end_headers = lambda: None
    getattr(h, f"do_{method}")()
    return sent["status"], json.loads(h.wfile.getvalue())


def test_serverless_handler():
    status, body = _call("POST", {"goal": "running shoes under $170", "budget": 200})
    assert status == 200 and body["live"] and body["events"]
    assert _call("POST", raw=b"{nope")[0] == 400
    assert _call("POST", raw=b"x" * 5000)[0] == 413
    assert _call("GET")[0] == 200
