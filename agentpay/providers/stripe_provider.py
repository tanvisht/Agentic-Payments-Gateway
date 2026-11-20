"""Stripe adapter (PaymentIntents API, off-session, merchant-initiated).

Agents never see card data: the principal saves a card once (SetupIntent /
Checkout in setup mode) and the gateway charges the saved `pm_...` off-session
under the agent's mandate. Stripe's own idempotency keys are derived from ours,
so a retried gateway request can never double-charge.
"""

from __future__ import annotations

import os
from typing import Any

from agentpay.core.errors import CardDeclined, ProviderError, ValidationFailed
from agentpay.providers.base import AuthorizeRequest, PaymentProvider, ProviderResult, ProviderStatus, WebhookUpdate

_STATUS = {
    "requires_capture": ProviderStatus.AUTHORIZED,
    "succeeded": ProviderStatus.CAPTURED,
    "requires_action": ProviderStatus.REQUIRES_ACTION,
    "canceled": ProviderStatus.CANCELED,
}

_WEBHOOK_EVENTS = {
    "payment_intent.succeeded": ProviderStatus.CAPTURED,
    "payment_intent.amount_capturable_updated": ProviderStatus.AUTHORIZED,
    "payment_intent.canceled": ProviderStatus.CANCELED,
    "payment_intent.requires_action": ProviderStatus.REQUIRES_ACTION,
    "charge.refunded": ProviderStatus.REFUNDED,
}


def _d(obj: Any) -> Any:
    """stripe-python >= 15 objects are not dicts; normalize everything we read."""
    return obj.to_dict() if hasattr(obj, "to_dict") else obj


class StripeProvider(PaymentProvider):
    name = "stripe"

    def __init__(self, secret_key: str | None = None, webhook_secret: str | None = None, *,
                 allow_live: bool | None = None, client: Any = None) -> None:
        import stripe

        self._stripe = stripe
        key = secret_key or os.environ.get("STRIPE_SECRET_KEY", "")
        allow_live = allow_live if allow_live is not None else os.environ.get("AGENTPAY_ALLOW_LIVE_KEYS") == "true"
        if client is None:
            if not key:
                raise ValueError("STRIPE_SECRET_KEY is required for the stripe provider")
            if key.startswith(("sk_live_", "rk_live_")) and not allow_live:
                raise ValueError("live Stripe key refused; set AGENTPAY_ALLOW_LIVE_KEYS=true to opt in")
        self.client = client or stripe.StripeClient(key, max_network_retries=2)
        self.webhook_secret = webhook_secret or os.environ.get("STRIPE_WEBHOOK_SECRET")

    # -- helpers ------------------------------------------------------
    def _result(self, pi: Any) -> ProviderResult:
        pi = _d(pi)
        status = _STATUS.get(pi["status"])
        if status is None:
            raise ProviderError(f"unexpected stripe status {pi['status']}", details={"stripe_status": pi["status"]})
        next_action = None
        if status is ProviderStatus.REQUIRES_ACTION and pi.get("next_action"):
            na = pi["next_action"]
            url = (na.get("redirect_to_url") or {}).get("url")
            next_action = {"type": na.get("type"), **({"url": url} if url else {})}
        return ProviderResult(status, pi["id"], pi.get("amount_received") or pi["amount"], next_action, raw={"id": pi["id"], "status": pi["status"]})

    def _translate(self, exc: Exception) -> Exception:
        s = self._stripe
        if isinstance(exc, s.CardError):
            err = getattr(exc, "error", None)
            code = getattr(err, "decline_code", None) or getattr(exc, "code", None) or "card_declined"
            pi = _d(getattr(err, "payment_intent", None))
            details = {"provider_ref": pi["id"]} if pi else {}
            return CardDeclined(exc.user_message or str(exc), decline_code=code, details=details)
        if isinstance(exc, (s.RateLimitError, s.APIConnectionError)):
            return ProviderError(f"stripe transient error: {exc}", retryable=True)
        if isinstance(exc, s.InvalidRequestError):
            return ValidationFailed(getattr(exc, "user_message", None) or str(exc))
        if isinstance(exc, s.AuthenticationError):
            return ProviderError("stripe authentication failed", retryable=False)
        if isinstance(exc, s.APIError):
            return ProviderError(f"stripe api error: {exc}", retryable=True)
        return exc

    # -- operations ---------------------------------------------------
    def authorize(self, req: AuthorizeRequest) -> ProviderResult:
        params = {
            "amount": req.amount,
            "currency": req.currency.lower(),
            "payment_method": req.payment_method,
            "confirm": True,
            "off_session": True,
            "capture_method": "automatic" if req.capture else "manual",
            "description": req.description[:1000] or None,
            "metadata": {**req.metadata, "agentpay_intent": req.intent_id, "merchant_id": req.merchant_id},
        }
        customer = req.metadata.get("stripe_customer")
        if customer:
            params["customer"] = customer
        params = {k: v for k, v in params.items() if v is not None}
        try:
            pi = self.client.v1.payment_intents.create(params, {"idempotency_key": f"auth:{req.idempotency_key}"})
        except Exception as exc:  # noqa: BLE001
            err = self._translate(exc)
            # off_session + SCA → Stripe raises CardError(authentication_required) with the PI attached
            if isinstance(err, CardDeclined) and err.decline_code == "authentication_required" and err.details.get("provider_ref"):
                return ProviderResult(ProviderStatus.REQUIRES_ACTION, err.details["provider_ref"], req.amount,
                                      next_action={"type": "authentication_required"})
            raise err from exc
        return self._result(pi)

    def capture(self, provider_ref: str, amount: int, idempotency_key: str) -> ProviderResult:
        try:
            pi = self.client.v1.payment_intents.capture(provider_ref, {"amount_to_capture": amount},
                                                        {"idempotency_key": f"cap:{idempotency_key}"})
        except Exception as exc:  # noqa: BLE001
            raise self._translate(exc) from exc
        return self._result(pi)

    def cancel(self, provider_ref: str, idempotency_key: str) -> ProviderResult:
        try:
            pi = self.client.v1.payment_intents.cancel(provider_ref, {}, {"idempotency_key": f"can:{idempotency_key}"})
        except Exception as exc:  # noqa: BLE001
            raise self._translate(exc) from exc
        return self._result(pi)

    def refund(self, provider_ref: str, amount: int, idempotency_key: str, reason: str | None = None) -> ProviderResult:
        params: dict[str, Any] = {"payment_intent": provider_ref, "amount": amount}
        if reason in ("duplicate", "fraudulent", "requested_by_customer"):
            params["reason"] = reason
        try:
            r = self.client.v1.refunds.create(params, {"idempotency_key": f"ref:{idempotency_key}"})
        except Exception as exc:  # noqa: BLE001
            raise self._translate(exc) from exc
        r = _d(r)
        if r["status"] == "failed":
            raise ProviderError("refund failed", details={"refund": r["id"]})
        return ProviderResult(ProviderStatus.REFUNDED, r["id"], r["amount"], raw={"status": r["status"]})

    def retrieve(self, provider_ref: str) -> ProviderResult:
        try:
            return self._result(self.client.v1.payment_intents.retrieve(provider_ref))
        except Exception as exc:  # noqa: BLE001
            raise self._translate(exc) from exc

    def create_customer_setup(self, principal_id: str, email: str | None, idempotency_key: str) -> dict:
        try:
            cust = _d(self.client.v1.customers.create(
                {k: v for k, v in {"email": email, "metadata": {"agentpay_principal": principal_id}}.items() if v},
                {"idempotency_key": f"cus:{idempotency_key}"}))
            si = _d(self.client.v1.setup_intents.create(
                {"customer": cust["id"], "usage": "off_session", "automatic_payment_methods": {"enabled": True},
                 "metadata": {"agentpay_principal": principal_id}},
                {"idempotency_key": f"seti:{idempotency_key}"}))
        except Exception as exc:  # noqa: BLE001
            raise self._translate(exc) from exc
        return {"customer": cust["id"], "setup_intent": si["id"], "client_secret": si["client_secret"]}

    def attach_result(self, setup_ref: str) -> dict:
        try:
            si = _d(self.client.v1.setup_intents.retrieve(setup_ref))
        except Exception as exc:  # noqa: BLE001
            raise self._translate(exc) from exc
        pm = si.get("payment_method")
        return {"status": si["status"], "payment_method": pm["id"] if isinstance(pm, dict) else pm, "customer": si.get("customer")}

    def parse_webhook(self, payload: bytes, signature_header: str | None) -> WebhookUpdate | None:
        if not self.webhook_secret:
            raise ProviderError("STRIPE_WEBHOOK_SECRET not configured")
        try:
            event = self._stripe.Webhook.construct_event(payload, signature_header or "", self.webhook_secret)
        except Exception as exc:  # noqa: BLE001 — SignatureVerificationError / ValueError
            from agentpay.core.errors import SignatureError
            raise SignatureError(f"stripe webhook signature invalid: {exc}") from exc
        event = _d(event)
        obj = event["data"]["object"]
        ref = obj.get("payment_intent") if event["type"].startswith("charge.") else obj.get("id")
        if not ref:
            return None
        return WebhookUpdate(provider_ref=ref, status=_WEBHOOK_EVENTS.get(event["type"]), event_type=event["type"],
                             raw={"id": event["id"]})
