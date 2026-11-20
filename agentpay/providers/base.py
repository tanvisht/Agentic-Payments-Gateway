"""Provider abstraction.

The gateway speaks one normalized vocabulary; each adapter translates to a
processor (Stripe today; Adyen, Braintree, Checkout.com, x402/stablecoin rails
tomorrow). Adding a processor = implementing these five methods.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ProviderStatus(str, Enum):
    AUTHORIZED = "authorized"          # funds held, awaiting capture
    CAPTURED = "captured"
    REQUIRES_ACTION = "requires_action"
    CANCELED = "canceled"
    REFUNDED = "refunded"


@dataclass(slots=True)
class AuthorizeRequest:
    intent_id: str
    amount: int
    currency: str
    payment_method: str
    capture: bool
    idempotency_key: str
    description: str = ""
    merchant_id: str = ""
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class ProviderResult:
    status: ProviderStatus
    provider_ref: str
    amount: int
    next_action: dict[str, Any] | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class WebhookUpdate:
    """A normalized provider webhook: which provider object changed and to what."""

    provider_ref: str
    status: ProviderStatus | None
    event_type: str
    raw: dict[str, Any] = field(default_factory=dict)


class PaymentProvider(ABC):
    name: str
    supported_currencies: frozenset[str] | None = None  # None = all

    def supports(self, currency: str) -> bool:
        return self.supported_currencies is None or currency in self.supported_currencies

    @abstractmethod
    def authorize(self, req: AuthorizeRequest) -> ProviderResult: ...

    @abstractmethod
    def capture(self, provider_ref: str, amount: int, idempotency_key: str) -> ProviderResult: ...

    @abstractmethod
    def cancel(self, provider_ref: str, idempotency_key: str) -> ProviderResult: ...

    @abstractmethod
    def refund(self, provider_ref: str, amount: int, idempotency_key: str, reason: str | None = None) -> ProviderResult: ...

    @abstractmethod
    def retrieve(self, provider_ref: str) -> ProviderResult: ...

    def parse_webhook(self, payload: bytes, signature_header: str | None) -> WebhookUpdate | None:  # pragma: no cover
        return None
