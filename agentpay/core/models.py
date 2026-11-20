"""Domain models. Persisted as JSON documents by the storage layer."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from agentpay.core.ids import new_id, now


class IntentStatus(str, Enum):
    REQUIRES_CONFIRMATION = "requires_confirmation"
    PROCESSING = "processing"
    REQUIRES_ACTION = "requires_action"      # e.g. 3DS step-up — escalate to the human principal
    REQUIRES_CAPTURE = "requires_capture"    # authorized, funds held
    SUCCEEDED = "succeeded"                  # captured
    PARTIALLY_REFUNDED = "partially_refunded"
    REFUNDED = "refunded"
    CANCELED = "canceled"
    FAILED = "failed"


class Principal(BaseModel):
    """The human (or org) on whose behalf agents spend. Owns the signing key for mandates."""

    id: str = Field(default_factory=lambda: new_id("prn"))
    name: str
    public_key: str  # base64 raw Ed25519
    default_payment_method: str | None = None
    email: str | None = None
    provider_customers: dict[str, str] = Field(default_factory=dict)   # provider -> customer id
    pending_setups: dict[str, str] = Field(default_factory=dict)       # provider -> setup intent id
    created_at: int = Field(default_factory=now)


class Agent(BaseModel):
    id: str = Field(default_factory=lambda: new_id("agt"))
    principal_id: str
    name: str
    public_key: str           # base64 raw Ed25519 — signs every payment request
    api_key_hash: str
    status: str = "active"    # active | suspended
    created_at: int = Field(default_factory=now)


class MandateScope(BaseModel):
    currency: str
    max_amount_per_txn: int
    max_total_amount: int
    merchants_allowed: list[str] = Field(default_factory=list)   # empty = any
    categories_allowed: list[str] = Field(default_factory=list)  # empty = any
    max_txn_count: int | None = None
    requires_human_above: int | None = None  # amounts above → requires_action


class Mandate(BaseModel):
    """A principal-signed, scoped, expiring delegation of spending authority to one agent."""

    id: str = Field(default_factory=lambda: new_id("mdt"))
    principal_id: str
    agent_id: str
    scope: MandateScope
    description: str = ""
    nonce: str
    not_before: int
    expires_at: int
    signature: str
    status: str = "active"  # active | revoked | exhausted
    spent_amount: int = 0
    reserved_amount: int = 0
    txn_count: int = 0
    created_at: int = Field(default_factory=now)

    def remaining(self) -> int:
        return self.scope.max_total_amount - self.spent_amount - self.reserved_amount


class PaymentIntent(BaseModel):
    id: str = Field(default_factory=lambda: new_id("pi"))
    agent_id: str
    principal_id: str
    mandate_id: str
    amount: int
    currency: str
    merchant_id: str
    category: str | None = None
    description: str = ""
    payment_method: str | None = None
    status: IntentStatus = IntentStatus.REQUIRES_CONFIRMATION
    capture_method: str = "automatic"  # automatic | manual
    provider: str | None = None
    provider_ref: str | None = None
    amount_captured: int = 0
    amount_refunded: int = 0
    risk: dict[str, Any] = Field(default_factory=dict)
    last_error: dict[str, Any] | None = None
    next_action: dict[str, Any] | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    attempts: list[dict[str, Any]] = Field(default_factory=list)
    created_at: int = Field(default_factory=now)
    updated_at: int = Field(default_factory=now)


class Refund(BaseModel):
    id: str = Field(default_factory=lambda: new_id("re"))
    payment_intent_id: str
    amount: int
    currency: str
    reason: str = "requested_by_customer"
    status: str = "succeeded"
    provider_ref: str | None = None
    created_at: int = Field(default_factory=now)


class Event(BaseModel):
    id: str = Field(default_factory=lambda: new_id("evt"))
    type: str
    data: dict[str, Any]
    created_at: int = Field(default_factory=now)


class WebhookEndpoint(BaseModel):
    id: str = Field(default_factory=lambda: new_id("we"))
    url: str
    secret: str
    events: list[str] = Field(default_factory=lambda: ["*"])
    created_at: int = Field(default_factory=now)
