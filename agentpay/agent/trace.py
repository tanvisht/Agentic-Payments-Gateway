"""Run one complete, real purchase in-process and return it as a replayable trace.

Used by the website's live demo endpoint (api/run.py) and by
scripts/record_trace.py. Each call builds a fresh in-memory gateway, so runs
are isolated and nothing persists between visitors.
"""

from __future__ import annotations

import os
import time
from typing import Any

MAX_GOAL = 160
DEFAULT_GOAL = "Buy noise cancelling headphones under $400"


def run_trace(goal: str = DEFAULT_GOAL, *, budget_cents: int = 50000) -> dict[str, Any]:
    os.environ.setdefault("AGENTPAY_LOG_LEVEL", "WARNING")
    from fastapi.testclient import TestClient

    from agentpay.agent.acp_client import AcpClient
    from agentpay.agent.demo import bootstrap, in_process_client
    from agentpay.agent.runner import run_acp_deterministic
    from agentpay.agent.tools import ToolExecutor
    from agentpay.api.app import create_app
    from agentpay.config import Settings
    from agentpay.gateway import Gateway

    goal = " ".join(str(goal or DEFAULT_GOAL).split())[:MAX_GOAL] or DEFAULT_GOAL
    budget_cents = max(100, min(int(budget_cents), 150000))

    gw = Gateway.build(Settings(admin_key="demo", async_webhooks=False))
    app = create_app(gw)
    t0 = time.perf_counter()
    events: list[dict[str, Any]] = []

    def add(actor: str, kind: str, data: dict) -> None:
        events.append({"t": round((time.perf_counter() - t0) * 1000, 1), "actor": actor, "kind": kind, "data": data})

    gw.events.subscribe_local(lambda e: add("gateway", e.type, e.data))
    _, _, agent, akp, key, mandate = bootstrap(gw, per_txn=budget_cents, total=max(budget_cents, 150000))
    add("principal", "mandate.signed", {"mandate_id": mandate.id, "scope": mandate.scope.model_dump(),
                                        "signature": mandate.signature[:24] + "…"})
    acp = AcpClient(TestClient(app))
    acp.connect(agent.id)
    add("merchant", "acp.session", {"session_id": acp.session_id, "merchant_key": (acp.merchant_key or "")[:16] + "…"})
    tools = ToolExecutor(in_process_client(app, key, akp), mandate.id, acp=acp, on_event=lambda k, d: add("agent", k, d))
    result = run_acp_deterministic(goal, tools, on_event=lambda k, d: add("agent", k, d),
                                   answers=["size 10", f"budget ${budget_cents / 100:.0f}"])
    intents = gw.payments.list_for_agent(agent.id)
    if intents:
        pi = intents[0]
        add("gateway", "ledger", {"entries": gw.ledger.entries(pi.id), "trial_balance": gw.ledger.trial_balance()})
        rec = gw.receipt(pi.id)
        add("gateway", "receipt", {"signature": rec["signature"][:24] + "…", "mandate_sha256": rec["mandate"]["sha256"][:16] + "…"})
    return {"goal": goal, "result": result, "recorded_at": int(time.time()), "live": True, "events": events}
