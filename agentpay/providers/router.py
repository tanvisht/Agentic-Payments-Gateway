"""Provider routing with ordered failover and per-provider circuit breakers.

Only *retryable* errors (timeouts, 5xx, rate limits) fail over. A hard decline
is the issuer's answer and is returned as-is — retrying it on another
processor is both pointless and a card-network compliance problem.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from agentpay.core.errors import NoProviderAvailable, ProviderError
from agentpay.providers.base import AuthorizeRequest, PaymentProvider, ProviderResult


@dataclass
class CircuitBreaker:
    threshold: int = 3
    cooldown_s: float = 30.0
    failures: int = 0
    opened_at: float | None = None
    clock: callable = field(default=time.monotonic)

    def allow(self) -> bool:
        if self.opened_at is None:
            return True
        if self.clock() - self.opened_at >= self.cooldown_s:
            return True  # half-open: let one probe through
        return False

    def success(self) -> None:
        self.failures, self.opened_at = 0, None

    def failure(self) -> None:
        self.failures += 1
        if self.failures >= self.threshold:
            self.opened_at = self.clock()

    @property
    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        return "half_open" if self.allow() else "open"


class ProviderRouter:
    def __init__(self, providers: list[PaymentProvider], *, breaker_threshold: int = 3, cooldown_s: float = 30.0) -> None:
        if not providers:
            raise ValueError("at least one provider is required")
        self.providers = {p.name: p for p in providers}
        self.order = [p.name for p in providers]
        self.breakers = {p.name: CircuitBreaker(breaker_threshold, cooldown_s) for p in providers}

    def get(self, name: str) -> PaymentProvider:
        try:
            return self.providers[name]
        except KeyError:
            raise NoProviderAvailable(f"provider {name!r} is not configured") from None

    def candidates(self, currency: str, preferred: str | None = None) -> list[PaymentProvider]:
        names = list(self.order)
        if preferred:
            if preferred not in self.providers:
                raise NoProviderAvailable(f"provider {preferred!r} is not configured")
            names.remove(preferred)
            names.insert(0, preferred)
        return [self.providers[n] for n in names if self.providers[n].supports(currency)]

    def authorize(self, req: AuthorizeRequest, preferred: str | None = None) -> tuple[PaymentProvider, ProviderResult, list[dict]]:
        attempts: list[dict] = []
        last: ProviderError | None = None
        for p in self.candidates(req.currency, preferred):
            br = self.breakers[p.name]
            if not br.allow():
                attempts.append({"provider": p.name, "outcome": "skipped_circuit_open"})
                continue
            try:
                res = p.authorize(req)
            except ProviderError as exc:
                if exc.retryable:
                    br.failure()
                    attempts.append({"provider": p.name, "outcome": "retryable_error", "message": exc.message})
                    last = exc
                    continue
                br.success()  # the processor is healthy; the card isn't
                attempts.append({"provider": p.name, "outcome": "declined", "code": exc.decline_code or exc.code})
                exc.details.setdefault("attempts", attempts)
                exc.details.setdefault("provider", p.name)
                raise
            br.success()
            attempts.append({"provider": p.name, "outcome": res.status.value})
            return p, res, attempts
        raise NoProviderAvailable(
            "all payment providers failed or are unavailable",
            details={"attempts": attempts, **({"last_error": last.message} if last else {})},
        )

    def health(self) -> dict[str, str]:
        return {n: self.breakers[n].state for n in self.order}
