"""Identity and reputation for agents and merchants.

Identity: an *agent card* (name, endpoint, capabilities, agent public key)
signed by the owning principal, so anyone can check who stands behind an agent.
Merchants register a signing key the same way.

Reputation: counterparties leave *signed feedback* that must reference a real,
settled payment between exactly those two parties (no payment, no review;
one review per payment). Scores aggregate with a Bayesian prior so a single
review can't swing a new agent to 0 or 100. The risk engine reads the score.

The data model mirrors ERC-8004's identity / reputation registries, so cards
and feedback can be anchored on-chain later without changing callers.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from agentpay.core import crypto
from agentpay.core.errors import NotFound, SignatureError, ValidationFailed
from agentpay.core.ids import new_id, now
from agentpay.core.models import IntentStatus, PaymentIntent, Principal
from agentpay.storage import Store

PRIOR_MEAN, PRIOR_WEIGHT = 70.0, 5.0


class AgentCard(BaseModel):
    id: str                                   # = agent id
    name: str
    description: str = ""
    endpoint: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    agent_public_key: str
    principal_id: str
    signature: str                            # principal's signature over card_payload
    created_at: int = Field(default_factory=now)

    def payload(self) -> dict[str, Any]:
        return {"type": "agentpay.agent_card.v1", **self.model_dump(exclude={"signature", "created_at"})}


class MerchantIdentity(BaseModel):
    id: str                                   # merchant_id, e.g. stride.run
    public_key: str
    display_name: str = ""
    created_at: int = Field(default_factory=now)


class Feedback(BaseModel):
    id: str = Field(default_factory=lambda: new_id("fb"))
    subject_agent_id: str
    merchant_id: str
    payment_intent_id: str
    score: int = Field(ge=0, le=100)
    tags: list[str] = Field(default_factory=list)
    comment: str = Field(default="", max_length=500)
    signature: str
    created_at: int = Field(default_factory=now)

    def payload(self) -> dict[str, Any]:
        return {"type": "agentpay.feedback.v1",
                **self.model_dump(include={"subject_agent_id", "merchant_id", "payment_intent_id", "score", "tags", "comment"})}


class Registry:
    def __init__(self, store: Store) -> None:
        self.store = store

    # -- identity -----------------------------------------------------------
    def register_card(self, card: AgentCard) -> AgentCard:
        principal = self.store.get("principal", card.principal_id, Principal)
        if not principal:
            raise NotFound("principal not found")
        if not crypto.verify(principal.public_key, card.payload(), card.signature):
            raise SignatureError("agent card must be signed by its principal")
        self.store.put("agent_card", card, k1=card.principal_id)
        return card

    def card(self, agent_id: str) -> AgentCard:
        c = self.store.get("agent_card", agent_id, AgentCard)
        if not c:
            raise NotFound("agent card not found")
        return c

    def register_merchant(self, merchant_id: str, public_key: str, display_name: str = "") -> MerchantIdentity:
        if len(crypto.b64d(public_key)) != 32:
            raise ValidationFailed("public_key must be a raw Ed25519 key")
        m = MerchantIdentity(id=merchant_id, public_key=public_key, display_name=display_name)
        self.store.put("merchant_identity", m)
        return m

    # -- reputation ---------------------------------------------------------
    def submit_feedback(self, fb: Feedback) -> Feedback:
        merchant = self.store.get("merchant_identity", fb.merchant_id, MerchantIdentity)
        if not merchant:
            raise NotFound("merchant is not registered")
        if not crypto.verify(merchant.public_key, fb.payload(), fb.signature):
            raise SignatureError("feedback signature does not verify against merchant key")
        pi = self.store.get("payment_intent", fb.payment_intent_id, PaymentIntent)
        if (not pi or pi.agent_id != fb.subject_agent_id or pi.merchant_id != fb.merchant_id
                or pi.status not in (IntentStatus.SUCCEEDED, IntentStatus.PARTIALLY_REFUNDED, IntentStatus.REFUNDED)):
            raise ValidationFailed("feedback must reference a settled payment between this agent and merchant")
        with self.store.tx():
            if self.store.find_by_field("feedback", "payment_intent_id", pi.id, Feedback):
                raise ValidationFailed("feedback already submitted for this payment")
            self.store.put("feedback", fb, k1=fb.subject_agent_id)
        return fb

    def reputation(self, agent_id: str) -> dict[str, Any]:
        fbs = self.store.list("feedback", Feedback, k1=agent_id, limit=1000)
        n = len(fbs)
        total = sum(f.score for f in fbs)
        score = (PRIOR_MEAN * PRIOR_WEIGHT + total) / (PRIOR_WEIGHT + n)
        return {"agent_id": agent_id, "score": round(score, 2), "count": n,
                "raw_mean": round(total / n, 2) if n else None,
                "recent": [f.model_dump(include={"merchant_id", "score", "tags", "created_at"}) for f in fbs[:5]]}
