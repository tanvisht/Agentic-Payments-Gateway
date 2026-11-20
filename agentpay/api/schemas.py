from __future__ import annotations

from pydantic import BaseModel, Field

from agentpay.core.models import MandateScope


class PrincipalIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    public_key: str
    default_payment_method: str | None = None
    email: str | None = Field(default=None, max_length=320)


class SetupIn(BaseModel):
    provider: str | None = None


class AgentIn(BaseModel):
    principal_id: str
    name: str = Field(min_length=1, max_length=200)
    public_key: str


class AgentStatusIn(BaseModel):
    status: str


class MandateIn(BaseModel):
    principal_id: str
    agent_id: str
    scope: MandateScope
    nonce: str = Field(min_length=8, max_length=128)
    not_before: int
    expires_at: int
    signature: str
    description: str = ""


class PaymentIntentIn(BaseModel):
    mandate_id: str
    amount: int = Field(gt=0, le=10**12)
    currency: str
    merchant_id: str = Field(min_length=1, max_length=200)
    category: str | None = None
    description: str = Field(default="", max_length=1000)
    payment_method: str | None = None
    capture_method: str = "automatic"
    provider: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict, max_length=20)


class CaptureIn(BaseModel):
    amount: int | None = Field(default=None, gt=0)


class RefundIn(BaseModel):
    amount: int | None = Field(default=None, gt=0)
    reason: str = "requested_by_customer"


class ApproveIn(BaseModel):
    signature: str


class RetryIn(BaseModel):
    payment_method: str | None = None


class WebhookEndpointIn(BaseModel):
    url: str
    events: list[str] | None = None
