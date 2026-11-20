import secrets
import time

import pytest
from fastapi.testclient import TestClient

from agentpay.agent.demo import bootstrap, in_process_client
from agentpay.api.app import create_app
from agentpay.config import Settings
from agentpay.gateway import Gateway
from agentpay.providers.mock import MockProvider
from agentpay.risk.engine import RiskConfig, RiskEngine

ADMIN = "test-admin"


import os

PG_DSN = os.environ.get("AGENTPAY_TEST_POSTGRES_DSN")


def _fresh_store():
    """SQLite in-memory by default; the whole suite reruns on Postgres when
    AGENTPAY_TEST_POSTGRES_DSN is set (CI does this)."""
    if not PG_DSN:
        return None
    from agentpay.storage.postgres import PostgresStore
    st = PostgresStore(PG_DSN)
    st.execute("TRUNCATE docs, idempotency, nonces, ledger")
    return st


@pytest.fixture
def gw():
    return Gateway.build(Settings(admin_key=ADMIN, async_webhooks=False, default_provider="mock"), store=_fresh_store(),
                         providers=[MockProvider("mock"), MockProvider("mock_backup")],
                         risk=RiskEngine(RiskConfig(velocity_max=50)))


@pytest.fixture
def app(gw):
    return create_app(gw)


@pytest.fixture
def admin(app):
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {ADMIN}"
    return c


@pytest.fixture
def world(gw, app):
    principal, pkp, agent, akp, api_key, mandate = bootstrap(gw)
    client = in_process_client(app, api_key, akp)
    return {"principal": principal, "pkp": pkp, "agent": agent, "akp": akp, "api_key": api_key,
            "mandate": mandate, "client": client}


def pay(world, **kw):
    body = {"mandate_id": world["mandate"].id, "amount": 5000, "currency": "USD",
            "merchant_id": "nike.com", "category": "running"}
    body.update(kw)
    return world["client"].create_payment(idempotency_key=kw.pop("_idem", None) or secrets.token_hex(8), **body)
