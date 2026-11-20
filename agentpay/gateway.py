"""Composition root: wires store, providers, mandates, risk, ledger and events."""

from __future__ import annotations

from dataclasses import dataclass

from agentpay.config import Settings
from agentpay.core import crypto
from agentpay.core.errors import NotFound, ValidationFailed
from agentpay.core.idempotency import Idempotency
from agentpay.core.models import Agent, Principal, WebhookEndpoint
from agentpay.core.payments import PaymentService
from agentpay.ledger.ledger import Ledger
from agentpay.mandates.service import MandateService
from agentpay.providers.base import PaymentProvider
from agentpay.providers.mock import MockProvider
from agentpay.providers.router import ProviderRouter
from agentpay.risk.engine import RiskEngine
from agentpay.storage import Store
from agentpay.webhooks.dispatcher import EventBus


def _build_providers(settings: Settings) -> list[PaymentProvider]:
    names = settings.providers or [settings.default_provider]
    out: list[PaymentProvider] = []
    for n in [x.strip() for x in names if x.strip()]:
        if n == "stripe":
            from agentpay.providers.stripe_provider import StripeProvider
            out.append(StripeProvider())
        elif n.startswith("mock"):
            out.append(MockProvider(n))
        else:
            raise ValueError(f"unknown provider {n!r}")
    return out


def _check_key(public_key: str) -> None:
    try:
        ok = len(crypto.b64d(public_key)) == 32
    except Exception:  # noqa: BLE001
        ok = False
    if not ok:
        raise ValidationFailed("public_key must be a base64url-encoded raw Ed25519 key (32 bytes)")


@dataclass
class Gateway:
    settings: Settings
    store: Store
    router: ProviderRouter
    mandates: MandateService
    ledger: Ledger
    risk: RiskEngine
    events: EventBus
    payments: PaymentService
    idempotency: Idempotency

    @classmethod
    def build(cls, settings: Settings | None = None, *, providers: list[PaymentProvider] | None = None,
              risk: RiskEngine | None = None, store: Store | None = None, events: EventBus | None = None) -> "Gateway":
        s = settings or Settings()
        st = store or Store(s.db_path)
        router = ProviderRouter(providers or _build_providers(s))
        mandates, ledger = MandateService(st), Ledger(st)
        rk = risk or RiskEngine()
        ev = events or EventBus(st, async_delivery=s.async_webhooks)
        default = s.default_provider if s.default_provider in router.providers else None
        payments = PaymentService(st, router, mandates, ledger, rk, ev, default_provider=default)
        return cls(s, st, router, mandates, ledger, rk, ev, payments, Idempotency(st))

    # -- registry ----------------------------------------------------
    def create_principal(self, name: str, public_key: str, default_payment_method: str | None = None) -> Principal:
        _check_key(public_key)
        p = Principal(name=name, public_key=public_key, default_payment_method=default_payment_method)
        self.store.put("principal", p)
        return p

    def get_principal(self, pid: str) -> Principal:
        p = self.store.get("principal", pid, Principal)
        if not p:
            raise NotFound(f"principal {pid} not found")
        return p

    def create_agent(self, principal_id: str, name: str, public_key: str) -> tuple[Agent, str]:
        self.get_principal(principal_id)
        _check_key(public_key)
        api_key = crypto.new_api_key("ak")
        a = Agent(principal_id=principal_id, name=name, public_key=public_key, api_key_hash=crypto.sha256_hex(api_key))
        self.store.put("agent", a, k1=a.api_key_hash)
        return a, api_key

    def get_agent(self, agent_id: str) -> Agent:
        a = self.store.get("agent", agent_id, Agent)
        if not a:
            raise NotFound(f"agent {agent_id} not found")
        return a

    def agent_by_api_key(self, api_key: str) -> Agent | None:
        return self.store.find_one("agent", crypto.sha256_hex(api_key), Agent)

    def set_agent_status(self, agent_id: str, status: str) -> Agent:
        if status not in ("active", "suspended"):
            raise ValidationFailed("status must be active or suspended")
        a = self.get_agent(agent_id)
        a.status = status
        self.store.put("agent", a, k1=a.api_key_hash)
        self.events.emit(f"agent.{status}", {"agent_id": a.id})
        return a

    def add_webhook_endpoint(self, url: str, events: list[str] | None = None) -> WebhookEndpoint:
        if not url.startswith(("https://", "http://")):
            raise ValidationFailed("url must be http(s)")
        ep = WebhookEndpoint(url=url, secret="whsec_" + crypto.new_api_key("x")[2:], events=events or ["*"])
        self.store.put("webhook_endpoint", ep)
        return ep
