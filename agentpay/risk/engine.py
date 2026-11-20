"""Rule-based risk engine for autonomous spend.

Agents fail differently from humans: runaway loops, prompt-injected merchants,
hallucinated amounts. Rules here target those modes. Each rule contributes a
score; the decision is `allow`, `review` (escalate to the principal, i.e.
requires_action) or `deny`.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

from agentpay.core.models import Mandate


@dataclass
class RiskDecision:
    decision: str  # allow | review | deny
    score: int
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"decision": self.decision, "score": self.score, "reasons": self.reasons}


@dataclass
class RiskConfig:
    velocity_window_s: int = 60
    velocity_max: int = 5               # payments per agent per window
    duplicate_window_s: int = 120       # same agent+merchant+amount
    review_ratio_of_txn_limit: float = 0.9
    deny_score: int = 80
    review_score: int = 40
    blocked_merchants: frozenset[str] = frozenset()


class RiskEngine:
    def __init__(self, config: RiskConfig | None = None, clock=time.time) -> None:
        self.cfg = config or RiskConfig()
        self.clock = clock
        self._recent: dict[str, deque] = defaultdict(deque)  # agent -> (ts, merchant, amount)

    def assess(self, *, agent_id: str, mandate: Mandate, amount: int, merchant_id: str) -> RiskDecision:
        t = self.clock()
        q = self._recent[agent_id]
        while q and t - q[0][0] > max(self.cfg.velocity_window_s, self.cfg.duplicate_window_s):
            q.popleft()

        score, reasons = 0, []
        if merchant_id in self.cfg.blocked_merchants:
            score += 100
            reasons.append("merchant_blocked")

        in_window = sum(1 for ts, *_ in q if t - ts <= self.cfg.velocity_window_s)
        if in_window >= self.cfg.velocity_max:
            score += 90
            reasons.append(f"velocity:{in_window + 1}_in_{self.cfg.velocity_window_s}s")

        dups = sum(1 for ts, m, a in q if m == merchant_id and a == amount and t - ts <= self.cfg.duplicate_window_s)
        if dups:
            score += 50
            reasons.append("possible_duplicate")

        if amount >= mandate.scope.max_amount_per_txn * self.cfg.review_ratio_of_txn_limit:
            score += 20
            reasons.append("near_per_txn_limit")

        if mandate.scope.max_total_amount and (mandate.spent_amount + mandate.reserved_amount + amount) / mandate.scope.max_total_amount > 0.95:
            score += 10
            reasons.append("mandate_nearly_exhausted")

        if mandate.scope.requires_human_above is not None and amount > mandate.scope.requires_human_above:
            score = max(score, self.cfg.review_score)
            reasons.append("above_human_approval_threshold")

        decision = "deny" if score >= self.cfg.deny_score else "review" if score >= self.cfg.review_score else "allow"
        if decision != "deny":
            q.append((t, merchant_id, amount))
        return RiskDecision(decision, min(score, 100), reasons)
