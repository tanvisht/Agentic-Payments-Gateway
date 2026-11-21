"""On-chain escrow rail (USDC-style ERC-20 + AgentEscrow contract).

Gateway lifecycle  →  contract call
  authorize        →  payer wallet: approve + lock(id, payee, token, amount, deadline, operator=gateway)
  capture(amount)  →  operator: release(id, amount)   (remainder returns to payer)
  cancel           →  operator: refund(id)
  (deadline)       →  anyone:   refund(id)           (funds can never be stranded)

`payment_method` for this rail is `escrow:<payer address>`; the payer wallet
must be one the gateway's custody/signing layer controls (in tests: unlocked
eth-tester accounts). Fiat minor units are converted to token units using the
token's decimals (USD cents → USDC 6dp: ×10^4).
"""

from __future__ import annotations

import hashlib

from agentpay.chain.evm import EvmChain
from agentpay.core.errors import CardDeclined, ProviderError, ValidationFailed
from agentpay.providers.base import AuthorizeRequest, PaymentProvider, ProviderResult, ProviderStatus

_STATUS = {1: ProviderStatus.AUTHORIZED, 2: ProviderStatus.CAPTURED, 3: ProviderStatus.CANCELED}


class EscrowProvider(PaymentProvider):
    name = "escrow"
    supported_currencies = frozenset({"USD"})

    def __init__(self, chain: EvmChain, escrow, token, *, merchant_addresses: dict[str, str],
                 token_decimals: int = 6, hold_seconds: int = 7 * 86400) -> None:
        self.chain, self.escrow, self.token = chain, escrow, token
        self.merchants = merchant_addresses
        self.scale = 10 ** (token_decimals - 2)
        self.hold_seconds = hold_seconds
        self._amounts: dict[str, int] = {}

    @staticmethod
    def escrow_id(key: str) -> bytes:
        return hashlib.sha256(key.encode()).digest()

    def _payer(self, pm: str) -> str:
        if not pm.startswith("escrow:"):
            raise ValidationFailed("escrow rail needs payment_method 'escrow:<payer address>'")
        return self.chain.w3.to_checksum_address(pm.split(":", 1)[1])

    def authorize(self, req: AuthorizeRequest) -> ProviderResult:
        payer = self._payer(req.payment_method)
        payee = self.merchants.get(req.merchant_id)
        if not payee:
            raise ValidationFailed(f"merchant {req.merchant_id} has no settlement address on the escrow rail")
        eid = self.escrow_id(req.idempotency_key)
        ref = "0x" + eid.hex()
        existing = self.escrow.functions.escrows(eid).call()
        if existing[6] != 0:  # idempotent replay
            return ProviderResult(_STATUS[existing[6]], ref, existing[4] // self.scale)
        units = req.amount * self.scale
        if self.token.functions.balanceOf(payer).call() < units:
            raise CardDeclined("insufficient token balance", decline_code="insufficient_funds")
        try:
            self.chain.send(self.token.functions.approve(self.escrow.address, units), payer)
            self.chain.send(self.escrow.functions.lock(eid, payee, self.token.address, units,
                                                       self.chain.now() + self.hold_seconds, self.chain.operator), payer)
            if req.capture:
                self.chain.send(self.escrow.functions.release(eid, units), self.chain.operator)
        except Exception as exc:  # noqa: BLE001 - RPC errors are retryable, reverts are not
            raise ProviderError(f"escrow transaction failed: {exc}", retryable=False) from exc
        return ProviderResult(ProviderStatus.CAPTURED if req.capture else ProviderStatus.AUTHORIZED, ref, req.amount)

    def capture(self, provider_ref: str, amount: int, idempotency_key: str) -> ProviderResult:
        eid = bytes.fromhex(provider_ref[2:])
        e = self.escrow.functions.escrows(eid).call()
        if e[6] == 2:
            return ProviderResult(ProviderStatus.CAPTURED, provider_ref, amount)
        if e[6] != 1:
            raise ProviderError("escrow is not capturable")
        self.chain.send(self.escrow.functions.release(eid, amount * self.scale), self.chain.operator)
        return ProviderResult(ProviderStatus.CAPTURED, provider_ref, amount)

    def cancel(self, provider_ref: str, idempotency_key: str) -> ProviderResult:
        eid = bytes.fromhex(provider_ref[2:])
        e = self.escrow.functions.escrows(eid).call()
        if e[6] == 3:
            return ProviderResult(ProviderStatus.CANCELED, provider_ref, e[4] // self.scale)
        if e[6] != 1:
            raise ProviderError("escrow is not cancelable")
        self.chain.send(self.escrow.functions.refund(eid), self.chain.operator)
        return ProviderResult(ProviderStatus.CANCELED, provider_ref, e[4] // self.scale)

    def refund(self, provider_ref: str, amount: int, idempotency_key: str, reason: str | None = None) -> ProviderResult:
        # Post-release refunds are a merchant-initiated token transfer back to the payer.
        raise ProviderError("refunds after release must be issued by the merchant on the escrow rail")

    def retrieve(self, provider_ref: str) -> ProviderResult:
        e = self.escrow.functions.escrows(bytes.fromhex(provider_ref[2:])).call()
        if e[6] == 0:
            raise ProviderError("unknown escrow")
        return ProviderResult(_STATUS[e[6]], provider_ref, e[4] // self.scale)
