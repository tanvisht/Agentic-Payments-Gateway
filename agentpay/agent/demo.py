"""End-to-end, in-process demo: principal signs a mandate, agent shops and pays."""

from __future__ import annotations

import secrets
import time

from agentpay.agent.client import GatewayClient
from agentpay.agent.runner import run_agent
from agentpay.agent.tools import ToolExecutor
from agentpay.api.app import create_app
from agentpay.config import Settings
from agentpay.core.crypto import KeyPair
from agentpay.core.models import MandateScope
from agentpay.gateway import Gateway
from agentpay.mandates.service import mandate_signing_payload


def bootstrap(gw: Gateway, *, per_txn: int = 20000, total: int = 50000, merchants: list[str] | None = None):
    principal_kp, agent_kp = KeyPair.generate(), KeyPair.generate()
    principal = gw.create_principal("Demo Principal", principal_kp.public_b64, default_payment_method="pm_card_visa")
    agent, api_key = gw.create_agent(principal.id, "shopping-agent", agent_kp.public_b64)
    scope = MandateScope(currency="USD", max_amount_per_txn=per_txn, max_total_amount=total,
                         merchants_allowed=merchants if merchants is not None else ["nike.com", "hoka.com", "adidas.com", "stride.run", "trailhead.co", "voltgear.shop", "sonicaudio.store"],
                         categories_allowed=["running", "apparel", "electronics", "accessories"])
    nb, exp, nonce = int(time.time()) - 5, int(time.time()) + 7 * 86400, secrets.token_hex(12)
    sig = principal_kp.sign(mandate_signing_payload(principal_id=principal.id, agent_id=agent.id, scope=scope,
                                                    nonce=nonce, not_before=nb, expires_at=exp))
    mandate = gw.mandates.register(principal=principal, agent=agent, scope=scope, nonce=nonce,
                                   not_before=nb, expires_at=exp, signature=sig)
    return principal, principal_kp, agent, agent_kp, api_key, mandate


def in_process_client(app, api_key: str, agent_kp: KeyPair) -> GatewayClient:
    """GatewayClient bound to an in-process ASGI app (no network)."""
    from fastapi.testclient import TestClient

    client = GatewayClient("http://testserver", api_key, agent_kp)
    client.http = TestClient(app)
    return client


def run_demo(goal: str) -> None:
    import os
    os.environ.setdefault("AGENTPAY_LOG_LEVEL", "WARNING")
    gw = Gateway.build(Settings(admin_key="demo", async_webhooks=False))
    app = create_app(gw)
    principal, _, agent, agent_kp, api_key, mandate = bootstrap(gw, per_txn=50000, total=150000)
    client = in_process_client(app, api_key, agent_kp)

    def log(kind: str, data: dict) -> None:
        print(f"  [{kind:13}] {data}")

    print(f"Principal {principal.id} signed mandate {mandate.id} for agent {agent.id}")
    print(f"Goal: {goal}\n")
    from fastapi.testclient import TestClient
    from agentpay.agent.acp_client import AcpClient
    from agentpay.agent.runner import run_acp_deterministic
    acp = AcpClient(TestClient(app))
    acp.connect(agent.id)
    tools = ToolExecutor(client, mandate.id, on_event=log, acp=acp)
    result = run_acp_deterministic(goal, tools, on_event=log, answers=["size 10"])
    print(f"\nResult: {result}")
    print(f"Trial balance (must be zero): {gw.ledger.trial_balance()}")
