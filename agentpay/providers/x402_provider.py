"""x402 rail behind the PaymentProvider interface.

`payment_method` = `x402:<base64 X-PAYMENT payload>`, signed by the agent's
(or principal's) wallet for exactly the intent amount to the merchant's
address. x402 is push-settled: there is no hold, so manual capture is
rejected and refunds are merchant-initiated transfers.
"""

from __future__ import annotations

from agentpay.core.errors import CardDeclined, ProviderError, ValidationFailed
from agentpay.providers.base import AuthorizeRequest, PaymentProvider, ProviderResult, ProviderStatus
from agentpay.x402.facilitator import Facilitator
from agentpay.x402.protocol import PaymentRequirements, decode_header


class X402Provider(PaymentProvider):
    name = "x402"
    supported_currencies = frozenset({"USD"})

    def __init__(self, facilitator: Facilitator, *, asset: str, network: str, merchant_addresses: dict[str, str],
                 token_decimals: int = 6) -> None:
        self.f, self.asset, self.network = facilitator, asset, network
        self.merchants = merchant_addresses
        self.scale = 10 ** (token_decimals - 2)
        self._settled: dict[str, ProviderResult] = {}

    def requirements(self, *, amount: int, merchant_id: str, resource: str, description: str = "") -> PaymentRequirements:
        pay_to = self.merchants.get(merchant_id)
        if not pay_to:
            raise ValidationFailed(f"merchant {merchant_id} has no x402 address")
        return PaymentRequirements(network=self.network, maxAmountRequired=str(amount * self.scale), resource=resource,
                                   description=description, payTo=pay_to, asset=self.asset)

    def authorize(self, req: AuthorizeRequest) -> ProviderResult:
        if req.idempotency_key in self._settled:
            return self._settled[req.idempotency_key]
        if not req.capture:
            raise ValidationFailed("x402 is push-settled; use capture_method=automatic")
        if not req.payment_method.startswith("x402:"):
            raise ValidationFailed("x402 rail needs payment_method 'x402:<X-PAYMENT payload>'")
        try:
            payment = decode_header(req.payment_method[5:])
        except ValueError as exc:
            raise ValidationFailed(str(exc)) from None
        reqs = self.requirements(amount=req.amount, merchant_id=req.merchant_id, resource=f"agentpay:{req.intent_id}")
        res = self.f.settle(payment, reqs)
        if not res.get("success"):
            reason = res.get("errorReason", "rejected")
            if reason == "insufficient_funds":
                raise CardDeclined("insufficient stablecoin balance", decline_code="insufficient_funds")
            raise CardDeclined(f"x402 payment rejected: {reason}", decline_code="x402_invalid")
        out = ProviderResult(ProviderStatus.CAPTURED, res["transaction"], req.amount, raw=res)
        self._settled[req.idempotency_key] = out
        return out

    def capture(self, provider_ref, amount, idempotency_key):
        raise ProviderError("x402 payments settle immediately")

    def cancel(self, provider_ref, idempotency_key):
        raise ProviderError("x402 payments cannot be voided")

    def refund(self, provider_ref, amount, idempotency_key, reason=None):
        raise ProviderError("x402 refunds are merchant-initiated transfers")

    def retrieve(self, provider_ref):
        for r in self._settled.values():
            if r.provider_ref == provider_ref:
                return r
        raise ProviderError("unknown x402 transaction")
