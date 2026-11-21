"""Intent manifest + audit trail.

For every mandate the gateway can answer, from its own records:
  manifest  - what the principal authorized (signed scope, window, purpose)
  activity  - every attempt the agent made, including ones that never became
              payments (mandate violations, risk declines), in order
  summary   - attempts / outcomes / spend / denials by rule / merchants touched
  compliance- each settled payment re-checked against the signed scope
  llm_cost  - what the agent's reasoning cost while acting under the mandate
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from pydantic import BaseModel, Field

from agentpay.core.ids import new_id, now
from agentpay.core.models import IntentStatus, Mandate, PaymentIntent
from agentpay.storage import Store

# USD per million tokens (input, output)
MODEL_PRICES = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-fable-5-2": (8.00, 40.00),
}


class AuditEntry(BaseModel):
    id: str = Field(default_factory=lambda: new_id("aud"))
    mandate_id: str
    kind: str                     # payment.attempt | payment.<status> | mandate.violation | risk.declined | ...
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: int = Field(default_factory=now)


class UsageRecord(BaseModel):
    id: str = Field(default_factory=lambda: new_id("use"))
    agent_id: str
    mandate_id: str | None = None
    session: str = ""
    model: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost_usd_micros: int = 0
    created_at: int = Field(default_factory=now)


def price(model: str, input_tokens: int, output_tokens: int) -> int:
    """Cost in micro-dollars (integer, like all money in the gateway)."""
    pin, pout = MODEL_PRICES.get(model, MODEL_PRICES["claude-opus-5-5"])
    return round(input_tokens * pin + output_tokens * pout)  # $/MTok == micro$/token


class AuditLog:
    def __init__(self, store: Store) -> None:
        self.store = store

    def record(self, mandate_id: str, kind: str, **data: Any) -> None:
        self.store.put("audit", AuditEntry(mandate_id=mandate_id, kind=kind, data=data), k1=mandate_id)

    def record_usage(self, u: UsageRecord) -> UsageRecord:
        u.cost_usd_micros = price(u.model, u.input_tokens, u.output_tokens)
        self.store.put("usage", u, k1=u.agent_id)
        return u

    def usage_summary(self, agent_id: str, mandate_id: str | None = None) -> dict[str, Any]:
        rows = [u for u in self.store.list("usage", UsageRecord, k1=agent_id, limit=10000)
                if mandate_id is None or u.mandate_id == mandate_id]
        by_model: dict[str, dict[str, int]] = {}
        for u in rows:
            m = by_model.setdefault(u.model, {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd_micros": 0})
            m["calls"] += 1
            m["input_tokens"] += u.input_tokens
            m["output_tokens"] += u.output_tokens
            m["cost_usd_micros"] += u.cost_usd_micros
        total = sum(m["cost_usd_micros"] for m in by_model.values())
        return {"calls": len(rows), "cost_usd": round(total / 1e6, 6), "by_model": by_model}

    def report(self, mandate: Mandate) -> dict[str, Any]:
        entries = sorted(self.store.list("audit", AuditEntry, k1=mandate.id, limit=10000), key=lambda e: (e.created_at, e.id))
        intents = [p for p in self.store.list("payment_intent", PaymentIntent, k1=mandate.agent_id, limit=10000)
                   if p.mandate_id == mandate.id]
        settled = [p for p in intents if p.status in (IntentStatus.SUCCEEDED, IntentStatus.PARTIALLY_REFUNDED, IntentStatus.REFUNDED)]
        denials = Counter(e.data.get("rule", "risk") for e in entries if e.kind in ("mandate.violation", "risk.declined"))
        sc = mandate.scope
        compliance = []
        for p in settled:
            issues = []
            if p.currency != sc.currency:
                issues.append("currency")
            if p.amount > sc.max_amount_per_txn:
                issues.append("per_txn_limit")
            if sc.merchants_allowed and p.merchant_id not in sc.merchants_allowed:
                issues.append("merchant")
            if sc.categories_allowed and (p.category or "") not in sc.categories_allowed:
                issues.append("category")
            if not mandate.not_before <= p.created_at < mandate.expires_at:
                issues.append("window")
            compliance.append({"payment_intent": p.id, "ok": not issues, "issues": issues})
        return {
            "manifest": {"mandate_id": mandate.id, "principal_id": mandate.principal_id, "agent_id": mandate.agent_id,
                         "purpose": mandate.description, "scope": sc.model_dump(), "not_before": mandate.not_before,
                         "expires_at": mandate.expires_at, "status": mandate.status, "signature": mandate.signature},
            "summary": {"attempts": sum(1 for e in entries if e.kind == "payment.attempt"),
                        "payments": Counter(p.status.value for p in intents),
                        "denied_by_rule": dict(denials),
                        "spent": mandate.spent_amount, "reserved": mandate.reserved_amount, "remaining": mandate.remaining(),
                        "merchants": sorted({p.merchant_id for p in settled})},
            "compliance": {"all_within_scope": all(c["ok"] for c in compliance), "payments": compliance},
            "llm_cost": self.usage_summary(mandate.agent_id, mandate.id),
            "activity": [e.model_dump() for e in entries],
        }
