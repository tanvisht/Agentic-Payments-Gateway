"""Run the agent purchase end-to-end against Stripe test mode.

    STRIPE_SECRET_KEY=sk_test_... python scripts/stripe_sandbox_e2e.py

Prints the Stripe PaymentIntent id so you can open it in the Dashboard.
"""

import os
import sys

from fastapi.testclient import TestClient

from agentpay.agent.acp_client import AcpClient
from agentpay.agent.demo import bootstrap, in_process_client
from agentpay.agent.runner import run_acp_deterministic
from agentpay.agent.tools import ToolExecutor
from agentpay.api.app import create_app
from agentpay.config import Settings
from agentpay.gateway import Gateway
from agentpay.providers.stripe_provider import StripeProvider


def main() -> int:
    key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not key.startswith("sk_test_"):
        print("Set STRIPE_SECRET_KEY to a test-mode key (sk_test_...)")
        return 2
    gw = Gateway.build(Settings(admin_key="x", async_webhooks=False, default_provider="stripe"),
                       providers=[StripeProvider(secret_key=key)])
    app = create_app(gw)
    _, _, agent, akp, api_key, mandate = bootstrap(gw)
    acp = AcpClient(TestClient(app))
    acp.connect(agent.id)
    tools = ToolExecutor(in_process_client(app, api_key, akp), mandate.id, acp=acp,
                         on_event=lambda k, d: print(f"[{k}] {d}"))
    print(run_acp_deterministic("running shoes under $170", tools, answers=["size 10"]))
    for pi in gw.payments.list_for_agent(agent.id):
        print(f"Stripe PaymentIntent: {pi.provider_ref} ({pi.status.value})  https://dashboard.stripe.com/test/payments/{pi.provider_ref}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
