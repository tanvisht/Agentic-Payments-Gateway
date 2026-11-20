"""PaymentService — the orchestration core of the gateway.

    agent request ─► mandate check ─► risk ─► reserve budget ─► route to provider
                                                                     │
          ledger + mandate settle ◄── state machine ◄── provider result

Network calls to processors happen *outside* database transactions; every
state change before and after is persisted, so a crash mid-flight leaves an
intent in `processing` that `sync()` (or the provider webhook) resolves.
"""

from __future__ import annotations

from typing import Any

from agentpay.core import crypto
from agentpay.core.errors import (
    CardDeclined, GatewayError, InvalidStateTransition, MandateViolation, NoProviderAvailable,
    NotFound, ProviderError, RiskDeclined, SignatureError, ValidationFailed,
)
from agentpay.core.models import Agent, IntentStatus as S, Mandate, PaymentIntent, Principal, Refund
from agentpay.core.money import normalize_currency
from agentpay.core.state_machine import transition
from agentpay.ledger.ledger import Ledger
from agentpay.mandates.service import MandateService
from agentpay.providers.base import AuthorizeRequest, ProviderResult, ProviderStatus, WebhookUpdate
from agentpay.providers.router import ProviderRouter
from agentpay.risk.engine import RiskEngine
from agentpay.storage import Store
from agentpay.webhooks.dispatcher import EventBus


def approval_payload(intent: PaymentIntent) -> dict[str, Any]:
    return {"type": "agentpay.approval.v1", "intent_id": intent.id, "amount": intent.amount,
            "currency": intent.currency, "merchant_id": intent.merchant_id}


class PaymentService:
    def __init__(self, store: Store, router: ProviderRouter, mandates: MandateService, ledger: Ledger,
                 risk: RiskEngine, events: EventBus, default_provider: str | None = None) -> None:
        self.store, self.router, self.mandates = store, router, mandates
        self.ledger, self.risk, self.events = ledger, risk, events
        self.default_provider = default_provider

    # -- persistence ----------------------------------------------------
    def get(self, intent_id: str) -> PaymentIntent:
        pi = self.store.get("payment_intent", intent_id, PaymentIntent)
        if not pi:
            raise NotFound(f"payment intent {intent_id} not found")
        return pi

    def _save(self, pi: PaymentIntent) -> None:
        self.store.put("payment_intent", pi, k1=pi.agent_id)

    def _emit(self, pi: PaymentIntent, type_: str, **extra: Any) -> None:
        self.events.emit(type_, {"payment_intent": pi.model_dump(mode="json"), **extra})

    def list_for_agent(self, agent_id: str, limit: int = 50) -> list[PaymentIntent]:
        return self.store.list("payment_intent", PaymentIntent, k1=agent_id, limit=limit)

    # -- create ---------------------------------------------------------
    def create(self, *, agent: Agent, mandate_id: str, amount: int, currency: str, merchant_id: str,
               category: str | None = None, description: str = "", payment_method: str | None = None,
               capture_method: str = "automatic", provider: str | None = None,
               metadata: dict[str, str] | None = None) -> PaymentIntent:
        if agent.status != "active":
            raise MandateViolation("agent is suspended", details={"rule": "agent_suspended"})
        if capture_method not in ("automatic", "manual"):
            raise ValidationFailed("capture_method must be automatic or manual")
        if not isinstance(amount, int) or isinstance(amount, bool):
            raise ValidationFailed("amount must be an integer in minor units")
        if not merchant_id or len(merchant_id) > 200:
            raise ValidationFailed("merchant_id is required")
        currency = normalize_currency(currency)
        principal = self.store.get("principal", agent.principal_id, Principal)
        pm = payment_method or (principal.default_payment_method if principal else None)
        if not pm:
            raise ValidationFailed("no payment_method supplied and principal has no default")
        provider = provider or self.default_provider

        with self.store.tx():
            mandate = self.mandates.get(mandate_id)
            self.mandates.check(mandate, agent_id=agent.id, amount=amount, currency=currency,
                                merchant_id=merchant_id, category=category)
            decision = self.risk.assess(agent_id=agent.id, mandate=mandate, amount=amount, merchant_id=merchant_id)
            if decision.decision == "deny":
                self.events.emit("risk.declined", {"agent_id": agent.id, "mandate_id": mandate.id, "amount": amount,
                                                   "merchant_id": merchant_id, "risk": decision.to_dict()})
                raise RiskDeclined("payment declined by risk engine", details=decision.to_dict())
            self.mandates.reserve(mandate.id, amount)
            pi = PaymentIntent(agent_id=agent.id, principal_id=agent.principal_id, mandate_id=mandate.id,
                               amount=amount, currency=currency, merchant_id=merchant_id, category=category,
                               description=description, payment_method=pm, capture_method=capture_method,
                               provider=provider, risk=decision.to_dict(), metadata=metadata or {})
            self._save(pi)
        self._emit(pi, "payment_intent.created")

        if decision.decision == "review":
            transition(pi, S.PROCESSING)
            transition(pi, S.REQUIRES_ACTION)
            pi.next_action = {"type": "principal_approval", "reasons": decision.reasons,
                              "approve_url": f"/v1/payment_intents/{pi.id}/approve"}
            self._save(pi)
            self._emit(pi, "payment_intent.requires_action")
            return pi
        return self._authorize(pi)

    # -- authorize --------------------------------------------------------
    def _authorize(self, pi: PaymentIntent) -> PaymentIntent:
        transition(pi, S.PROCESSING)
        pi.next_action = None
        self._save(pi)
        req = AuthorizeRequest(intent_id=pi.id, amount=pi.amount, currency=pi.currency, payment_method=pi.payment_method or "",
                               capture=pi.capture_method == "automatic", idempotency_key=f"{pi.id}:{len(pi.attempts)}",
                               description=pi.description, merchant_id=pi.merchant_id, metadata=dict(pi.metadata))
        try:
            provider, res, attempts = self.router.authorize(req, preferred=pi.provider)
        except (CardDeclined, ProviderError, NoProviderAvailable, ValidationFailed) as exc:
            return self._fail(pi, exc)
        pi.attempts.extend(attempts)
        pi.provider, pi.provider_ref = provider.name, res.provider_ref
        return self._apply_result(pi, res)

    def _fail(self, pi: PaymentIntent, exc: GatewayError) -> PaymentIntent:
        pi.attempts.extend(exc.details.get("attempts", []) if isinstance(exc.details, dict) else [])
        pi.last_error = {"code": exc.code, "message": exc.message,
                         **({"decline_code": exc.decline_code} if getattr(exc, "decline_code", None) else {})}
        with self.store.tx():
            transition(pi, S.FAILED)
            self.mandates.release(pi.mandate_id, pi.amount)
            self._save(pi)
        self._emit(pi, "payment_intent.payment_failed")
        return pi

    def _apply_result(self, pi: PaymentIntent, res: ProviderResult) -> PaymentIntent:
        with self.store.tx():
            if res.status is ProviderStatus.REQUIRES_ACTION:
                transition(pi, S.REQUIRES_ACTION)
                pi.next_action = res.next_action or {"type": "authentication_required"}
                self._save(pi)
                evt = "payment_intent.requires_action"
            elif res.status is ProviderStatus.AUTHORIZED:
                transition(pi, S.REQUIRES_CAPTURE)
                pi.next_action = None
                self.ledger.record_authorization(intent_id=pi.id, principal_id=pi.principal_id, provider=pi.provider or "",
                                                 amount=pi.amount, currency=pi.currency)
                self._save(pi)
                evt = "payment_intent.amount_capturable_updated"
            elif res.status is ProviderStatus.CAPTURED:
                transition(pi, S.SUCCEEDED)
                pi.next_action = None
                pi.amount_captured = res.amount or pi.amount
                self.ledger.record_capture(intent_id=pi.id, principal_id=pi.principal_id, provider=pi.provider or "",
                                           merchant_id=pi.merchant_id, authorized=pi.amount, captured=pi.amount_captured,
                                           currency=pi.currency, from_hold=False)
                self.mandates.settle(pi.mandate_id, pi.amount, pi.amount_captured)
                self._save(pi)
                evt = "payment_intent.succeeded"
            elif res.status is ProviderStatus.CANCELED:
                transition(pi, S.CANCELED)
                self.mandates.release(pi.mandate_id, pi.amount)
                self._save(pi)
                evt = "payment_intent.canceled"
            else:
                raise ProviderError(f"unexpected provider status {res.status}")
        self._emit(pi, evt)
        return pi

    # -- human-in-the-loop ------------------------------------------------
    def approve(self, intent_id: str, signature: str) -> PaymentIntent:
        """Principal approves a risk-escalated payment with their mandate key."""
        pi = self.get(intent_id)
        if pi.status is not S.REQUIRES_ACTION or (pi.next_action or {}).get("type") != "principal_approval":
            raise InvalidStateTransition("payment intent is not awaiting principal approval")
        principal = self.store.get("principal", pi.principal_id, Principal)
        if not principal or not crypto.verify(principal.public_key, approval_payload(pi), signature):
            raise SignatureError("approval signature does not verify against principal key")
        mandate = self.mandates.get(pi.mandate_id)
        if mandate.status == "revoked":
            raise MandateViolation("mandate was revoked", details={"rule": "inactive"})
        pi.metadata["approved_by"] = principal.id
        self._emit(pi, "payment_intent.approved")
        return self._authorize(pi)

    def retry(self, intent_id: str, *, agent: Agent, payment_method: str | None = None) -> PaymentIntent:
        pi = self.get(intent_id)
        if pi.agent_id != agent.id:
            raise NotFound(f"payment intent {intent_id} not found")
        if pi.status is not S.FAILED:
            raise InvalidStateTransition("only failed payment intents can be retried")
        with self.store.tx():
            m = self.mandates.get(pi.mandate_id)
            self.mandates.check(m, agent_id=agent.id, amount=pi.amount, currency=pi.currency,
                                merchant_id=pi.merchant_id, category=pi.category)
            self.mandates.reserve(m.id, pi.amount)
            if payment_method:
                pi.payment_method = payment_method
            pi.last_error = None
            self._save(pi)
        return self._authorize(pi)

    # -- capture / cancel / refund ------------------------------------------
    def capture(self, intent_id: str, amount: int | None = None) -> PaymentIntent:
        pi = self.get(intent_id)
        if pi.status is not S.REQUIRES_CAPTURE:
            raise InvalidStateTransition(f"cannot capture a payment in status {pi.status.value}")
        amt = pi.amount if amount is None else amount
        if amt <= 0 or amt > pi.amount:
            raise ValidationFailed("capture amount must be between 1 and the authorized amount")
        res = self.router.get(pi.provider or "").capture(pi.provider_ref or "", amt, f"{pi.id}:capture")
        with self.store.tx():
            transition(pi, S.SUCCEEDED)
            pi.amount_captured = res.amount or amt
            self.ledger.record_capture(intent_id=pi.id, principal_id=pi.principal_id, provider=pi.provider or "",
                                       merchant_id=pi.merchant_id, authorized=pi.amount, captured=pi.amount_captured,
                                       currency=pi.currency, from_hold=True)
            self.mandates.settle(pi.mandate_id, pi.amount, pi.amount_captured)
            self._save(pi)
        self._emit(pi, "payment_intent.succeeded")
        return pi

    def cancel(self, intent_id: str) -> PaymentIntent:
        pi = self.get(intent_id)
        if pi.status not in (S.REQUIRES_CONFIRMATION, S.REQUIRES_CAPTURE, S.REQUIRES_ACTION):
            raise InvalidStateTransition(f"cannot cancel a payment in status {pi.status.value}")
        was_held = pi.status is S.REQUIRES_CAPTURE
        if pi.provider_ref:
            self.router.get(pi.provider or "").cancel(pi.provider_ref, f"{pi.id}:cancel")
        with self.store.tx():
            transition(pi, S.CANCELED)
            pi.next_action = None
            if was_held:
                self.ledger.record_void(intent_id=pi.id, principal_id=pi.principal_id, provider=pi.provider or "",
                                        amount=pi.amount, currency=pi.currency)
            self.mandates.release(pi.mandate_id, pi.amount)
            self._save(pi)
        self._emit(pi, "payment_intent.canceled")
        return pi

    def refund(self, intent_id: str, amount: int | None = None, reason: str = "requested_by_customer") -> Refund:
        pi = self.get(intent_id)
        if pi.status not in (S.SUCCEEDED, S.PARTIALLY_REFUNDED):
            raise InvalidStateTransition(f"cannot refund a payment in status {pi.status.value}")
        refundable = pi.amount_captured - pi.amount_refunded
        amt = refundable if amount is None else amount
        if amt <= 0 or amt > refundable:
            raise ValidationFailed(f"refund amount must be between 1 and {refundable}")
        n = len(self.store.list("refund", Refund, k1=pi.id, limit=1000))
        res = self.router.get(pi.provider or "").refund(pi.provider_ref or "", amt, f"{pi.id}:refund:{n}", reason)
        with self.store.tx():
            pi.amount_refunded += amt
            transition(pi, S.REFUNDED if pi.amount_refunded == pi.amount_captured else S.PARTIALLY_REFUNDED)
            r = Refund(payment_intent_id=pi.id, amount=amt, currency=pi.currency, reason=reason, provider_ref=res.provider_ref)
            self.store.put("refund", r, k1=pi.id)
            self.ledger.record_refund(intent_id=pi.id, principal_id=pi.principal_id, merchant_id=pi.merchant_id,
                                      amount=amt, currency=pi.currency)
            self.mandates.credit_back(pi.mandate_id, amt)
            self._save(pi)
        self._emit(pi, "charge.refunded", refund=r.model_dump(mode="json"))
        return r

    # -- async provider updates ---------------------------------------------
    def sync(self, intent_id: str) -> PaymentIntent:
        """Pull the provider's view and reconcile (3DS completed, crash recovery)."""
        pi = self.get(intent_id)
        if not pi.provider_ref or pi.status not in (S.PROCESSING, S.REQUIRES_ACTION):
            return pi
        res = self.router.get(pi.provider or "").retrieve(pi.provider_ref)
        if res.status is ProviderStatus.REQUIRES_ACTION and pi.status is S.REQUIRES_ACTION:
            return pi
        return self._apply_result(pi, res)

    def apply_webhook(self, update: WebhookUpdate) -> PaymentIntent | None:
        pi = self.store.execute(
            "SELECT body FROM docs WHERE kind='payment_intent' AND json_extract(body,'$.provider_ref')=?",
            (update.provider_ref,),
        ).fetchone()
        if not pi:
            return None
        intent = PaymentIntent.model_validate_json(pi[0])
        if update.status in (ProviderStatus.CAPTURED, ProviderStatus.AUTHORIZED, ProviderStatus.CANCELED) \
                and intent.status in (S.PROCESSING, S.REQUIRES_ACTION):
            return self._apply_result(intent, ProviderResult(update.status, update.provider_ref, intent.amount))
        return intent  # already reconciled — webhooks are at-least-once
