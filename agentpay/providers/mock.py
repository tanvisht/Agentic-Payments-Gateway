"""Deterministic in-process processor for tests, local dev and the demo.

Magic payment methods mirror Stripe's test tokens so flows are portable:

    pm_card_visa                       → approves
    pm_card_chargeDeclined             → hard decline
    pm_card_chargeDeclinedInsufficientFunds → insufficient_funds
    pm_card_authenticationRequired     → requires_action (3DS)
    pm_card_processingError / pm_flaky → retryable provider error (exercises failover)
"""

from __future__ import annotations

import itertools
import threading

from agentpay.core.errors import CardDeclined, ProviderError, ValidationFailed
from agentpay.providers.base import AuthorizeRequest, PaymentProvider, ProviderResult, ProviderStatus


class MockProvider(PaymentProvider):
    def __init__(self, name: str = "mock", *, fail_all: bool = False) -> None:
        self.name = name
        self.fail_all = fail_all
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._objects: dict[str, dict] = {}
        self._idem: dict[str, ProviderResult] = {}
        self.calls: list[tuple[str, str]] = []

    def _remember(self, key: str, result: ProviderResult) -> ProviderResult:
        self._idem[key] = result
        return result

    def authorize(self, req: AuthorizeRequest) -> ProviderResult:
        with self._lock:
            self.calls.append(("authorize", req.payment_method))
            if req.idempotency_key in self._idem:
                return self._idem[req.idempotency_key]
            if self.fail_all:
                raise ProviderError(f"{self.name} unavailable", retryable=True)
            pm = req.payment_method
            if pm in ("pm_card_processingError", "pm_flaky"):
                raise ProviderError("processor error", retryable=True)
            if pm == "pm_card_chargeDeclined":
                raise CardDeclined("Your card was declined.", decline_code="generic_decline")
            if pm == "pm_card_chargeDeclinedInsufficientFunds":
                raise CardDeclined("Your card has insufficient funds.", decline_code="insufficient_funds")
            if not pm.startswith("pm_"):
                raise ValidationFailed(f"unknown payment method {pm!r}")
            ref = f"{self.name}_pi_{next(self._ids)}"
            if pm == "pm_card_authenticationRequired":
                obj = {"status": "requires_action", "amount": req.amount, "captured": 0, "refunded": 0}
                self._objects[ref] = obj
                return self._remember(req.idempotency_key, ProviderResult(
                    ProviderStatus.REQUIRES_ACTION, ref, req.amount,
                    next_action={"type": "redirect_to_url", "url": f"https://mock.agentpay.dev/3ds/{ref}"},
                ))
            status = ProviderStatus.CAPTURED if req.capture else ProviderStatus.AUTHORIZED
            self._objects[ref] = {"status": status.value, "amount": req.amount,
                                  "captured": req.amount if req.capture else 0, "refunded": 0}
            return self._remember(req.idempotency_key, ProviderResult(status, ref, req.amount))

    def complete_action(self, provider_ref: str, capture: bool) -> ProviderResult:
        """Simulates the human finishing 3DS."""
        obj = self._objects[provider_ref]
        obj["status"] = "captured" if capture else "authorized"
        if capture:
            obj["captured"] = obj["amount"]
        return ProviderResult(ProviderStatus(obj["status"]), provider_ref, obj["amount"])

    def capture(self, provider_ref: str, amount: int, idempotency_key: str) -> ProviderResult:
        with self._lock:
            if idempotency_key in self._idem:
                return self._idem[idempotency_key]
            obj = self._objects.get(provider_ref)
            if not obj or obj["status"] != "authorized":
                raise ProviderError("payment is not capturable")
            if amount > obj["amount"]:
                raise ValidationFailed("capture exceeds authorized amount")
            obj.update(status="captured", captured=amount)
            return self._remember(idempotency_key, ProviderResult(ProviderStatus.CAPTURED, provider_ref, amount))

    def cancel(self, provider_ref: str, idempotency_key: str) -> ProviderResult:
        obj = self._objects.get(provider_ref)
        if not obj or obj["status"] not in ("authorized", "requires_action"):
            raise ProviderError("payment is not cancelable")
        obj["status"] = "canceled"
        return ProviderResult(ProviderStatus.CANCELED, provider_ref, obj["amount"])

    def refund(self, provider_ref: str, amount: int, idempotency_key: str, reason: str | None = None) -> ProviderResult:
        with self._lock:
            if idempotency_key in self._idem:
                return self._idem[idempotency_key]
            obj = self._objects.get(provider_ref)
            if not obj or obj["status"] != "captured":
                raise ProviderError("payment is not refundable")
            if obj["refunded"] + amount > obj["captured"]:
                raise ValidationFailed("refund exceeds captured amount")
            obj["refunded"] += amount
            return self._remember(idempotency_key, ProviderResult(ProviderStatus.REFUNDED, f"{provider_ref}_re{obj['refunded']}", amount))

    def retrieve(self, provider_ref: str) -> ProviderResult:
        obj = self._objects[provider_ref]
        return ProviderResult(ProviderStatus(obj["status"]), provider_ref, obj["amount"])
