"""Mandates: principal-signed delegations of bounded spending authority.

Flow
----
1. The principal (human) builds a mandate payload — agent id, scope, validity
   window, nonce — and signs its canonical JSON with their Ed25519 key. The
   private key never reaches the gateway.
2. The gateway verifies the signature against the principal's registered
   public key and stores the mandate.
3. Every payment is checked against the mandate *and* atomically reserves
   budget, so concurrent agent requests can never overspend.
"""

from __future__ import annotations

from typing import Any

from agentpay.core import crypto
from agentpay.core.errors import MandateViolation, NotFound, SignatureError, ValidationFailed
from agentpay.core.ids import now
from agentpay.core.models import Agent, Mandate, MandateScope, Principal
from agentpay.core.money import normalize_currency
from agentpay.storage import Store

MAX_MANDATE_LIFETIME = 90 * 24 * 3600


def mandate_signing_payload(
    *, principal_id: str, agent_id: str, scope: MandateScope | dict, nonce: str, not_before: int, expires_at: int, description: str = ""
) -> dict[str, Any]:
    scope_d = scope.model_dump() if isinstance(scope, MandateScope) else MandateScope(**scope).model_dump()
    return {
        "type": "agentpay.mandate.v1",
        "principal_id": principal_id,
        "agent_id": agent_id,
        "scope": scope_d,
        "nonce": nonce,
        "not_before": not_before,
        "expires_at": expires_at,
        "description": description,
    }


class MandateService:
    def __init__(self, store: Store) -> None:
        self.store = store

    def register(self, *, principal: Principal, agent: Agent, scope: MandateScope, nonce: str,
                 not_before: int, expires_at: int, signature: str, description: str = "") -> Mandate:
        if agent.principal_id != principal.id:
            raise ValidationFailed("agent does not belong to principal")
        scope.currency = normalize_currency(scope.currency)
        if scope.max_amount_per_txn <= 0 or scope.max_total_amount <= 0:
            raise ValidationFailed("mandate limits must be positive")
        if scope.max_amount_per_txn > scope.max_total_amount:
            raise ValidationFailed("max_amount_per_txn cannot exceed max_total_amount")
        if expires_at <= not_before:
            raise ValidationFailed("expires_at must be after not_before")
        if expires_at - not_before > MAX_MANDATE_LIFETIME:
            raise ValidationFailed("mandate lifetime exceeds 90 days")
        if expires_at <= now():
            raise ValidationFailed("mandate already expired")

        payload = mandate_signing_payload(
            principal_id=principal.id, agent_id=agent.id, scope=scope, nonce=nonce,
            not_before=not_before, expires_at=expires_at, description=description,
        )
        if not crypto.verify(principal.public_key, payload, signature):
            raise SignatureError("mandate signature does not verify against principal key")
        if not self.store.claim_nonce(f"mandate:{principal.id}", nonce, expires_at, now()):
            raise SignatureError("mandate nonce already used")

        m = Mandate(
            principal_id=principal.id, agent_id=agent.id, scope=scope, nonce=nonce,
            not_before=not_before, expires_at=expires_at, signature=signature, description=description,
        )
        self.store.put("mandate", m, k1=agent.id)
        return m

    def get(self, mandate_id: str) -> Mandate:
        m = self.store.get("mandate", mandate_id, Mandate)
        if not m:
            raise NotFound(f"mandate {mandate_id} not found")
        return m

    def revoke(self, mandate_id: str) -> Mandate:
        with self.store.tx():
            m = self.get(mandate_id)
            m.status = "revoked"
            self.store.put("mandate", m, k1=m.agent_id)
        return m

    # -- enforcement --------------------------------------------------
    def check(self, m: Mandate, *, agent_id: str, amount: int, currency: str,
              merchant_id: str, category: str | None, at: int | None = None) -> None:
        t = now() if at is None else at
        def deny(reason: str, code: str) -> None:
            raise MandateViolation(reason, details={"rule": code, "mandate_id": m.id})

        if m.agent_id != agent_id:
            deny("mandate was issued to a different agent", "agent_mismatch")
        if m.status != "active":
            deny(f"mandate is {m.status}", "inactive")
        if t < m.not_before:
            deny("mandate not yet valid", "not_yet_valid")
        if t >= m.expires_at:
            deny("mandate expired", "expired")
        if currency != m.scope.currency:
            deny(f"mandate only allows {m.scope.currency}", "currency")
        if amount <= 0:
            deny("amount must be positive", "amount")
        if amount > m.scope.max_amount_per_txn:
            deny("amount exceeds per-transaction limit", "per_txn_limit")
        if amount > m.remaining():
            deny("amount exceeds remaining mandate budget", "budget")
        if m.scope.merchants_allowed and merchant_id not in m.scope.merchants_allowed:
            deny(f"merchant {merchant_id} not allowed by mandate", "merchant")
        if m.scope.categories_allowed and (category or "") not in m.scope.categories_allowed:
            deny(f"category {category!r} not allowed by mandate", "category")
        if m.scope.max_txn_count is not None and m.txn_count >= m.scope.max_txn_count:
            deny("mandate transaction count exhausted", "txn_count")

    def reserve(self, mandate_id: str, amount: int) -> Mandate:
        """Hold budget for an in-flight payment. Must be called inside a store transaction."""
        m = self.get(mandate_id)
        if amount > m.remaining():
            raise MandateViolation("amount exceeds remaining mandate budget", details={"rule": "budget", "mandate_id": m.id})
        m.reserved_amount += amount
        m.txn_count += 1
        self.store.put("mandate", m, k1=m.agent_id)
        return m

    def settle(self, mandate_id: str, reserved: int, spent: int) -> Mandate:
        """Convert a reservation into spend (spent may be < reserved on partial capture)."""
        m = self.get(mandate_id)
        m.reserved_amount = max(0, m.reserved_amount - reserved)
        m.spent_amount += spent
        if m.remaining() <= 0:
            m.status = "exhausted"
        self.store.put("mandate", m, k1=m.agent_id)
        return m

    def release(self, mandate_id: str, reserved: int, *, uncount: bool = True) -> Mandate:
        m = self.get(mandate_id)
        m.reserved_amount = max(0, m.reserved_amount - reserved)
        if uncount:
            m.txn_count = max(0, m.txn_count - 1)
        self.store.put("mandate", m, k1=m.agent_id)
        return m

    def credit_back(self, mandate_id: str, amount: int) -> Mandate:
        """Refunds restore budget."""
        m = self.get(mandate_id)
        m.spent_amount = max(0, m.spent_amount - amount)
        if m.status == "exhausted" and m.remaining() > 0 and m.expires_at > now():
            m.status = "active"
        self.store.put("mandate", m, k1=m.agent_id)
        return m
