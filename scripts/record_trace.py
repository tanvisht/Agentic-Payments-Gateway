"""Record a real end-to-end run (agent -> ACP merchant -> gateway -> ledger -> receipt) as web/trace.json.

The website's visualizer replays this file, so everything it shows is real gateway output.
"""

import json
import os
import time
from pathlib import Path

os.environ.setdefault("AGENTPAY_LOG_LEVEL", "WARNING")

from fastapi.testclient import TestClient  # noqa: E402

from agentpay.agent.acp_client import AcpClient  # noqa: E402
from agentpay.agent.demo import bootstrap, in_process_client  # noqa: E402
from agentpay.agent.runner import run_acp_deterministic  # noqa: E402
from agentpay.agent.tools import ToolExecutor  # noqa: E402
from agentpay.api.app import create_app  # noqa: E402
from agentpay.config import Settings  # noqa: E402
from agentpay.gateway import Gateway  # noqa: E402

GOAL = "Buy noise cancelling headphones under $400"


def main() -> None:
    gw = Gateway.build(Settings(admin_key="demo", async_webhooks=False))
    app = create_app(gw)
    t0 = time.perf_counter()
    events: list[dict] = []

    def add(actor: str, kind: str, data: dict) -> None:
        events.append({"t": round((time.perf_counter() - t0) * 1000, 1), "actor": actor, "kind": kind, "data": data})

    gw.events.subscribe_local(lambda e: add("gateway", e.type, e.data))
    principal, _, agent, akp, key, mandate = bootstrap(gw, per_txn=50000, total=150000)
    add("principal", "mandate.signed", {"mandate_id": mandate.id, "scope": mandate.scope.model_dump(),
                                        "signature": mandate.signature[:24] + "…"})
    acp = AcpClient(TestClient(app))
    acp.connect(agent.id)
    add("merchant", "acp.session", {"session_id": acp.session_id, "merchant_key": acp.merchant_key[:16] + "…"})
    tools = ToolExecutor(in_process_client(app, key, akp), mandate.id, acp=acp,
                         on_event=lambda k, d: add("agent", k, d))
    result = run_acp_deterministic(GOAL, tools, on_event=lambda k, d: add("agent", k, d))
    pi = gw.payments.list_for_agent(agent.id)[0]
    add("gateway", "ledger", {"entries": gw.ledger.entries(pi.id), "trial_balance": gw.ledger.trial_balance()})
    rec = gw.receipt(pi.id)
    add("gateway", "receipt", {"signature": rec["signature"][:24] + "…", "mandate_sha256": rec["mandate"]["sha256"][:16] + "…"})
    out = {"goal": GOAL, "result": result, "recorded_at": int(time.time()), "events": events}
    Path("web/trace.json").write_text(json.dumps(out, indent=1, default=str))
    print(f"wrote web/trace.json ({len(events)} events): {result}")


if __name__ == "__main__":
    main()
