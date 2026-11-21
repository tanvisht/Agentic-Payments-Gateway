"""Facilitators verify and settle x402 payments.

`LocalFacilitator` keeps token balances in memory with EIP-3009 semantics
(single-use nonces, validity window, exact signer); a production deployment
swaps in a facilitator that submits transferWithAuthorization on-chain.
"""

from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

from agentpay.x402.protocol import X402_VERSION, PaymentRequirements, recover_signer


@dataclass
class VerifyResult:
    valid: bool
    reason: str = ""
    payer: str = ""


class Facilitator(ABC):
    @abstractmethod
    def verify(self, payment: dict, req: PaymentRequirements) -> VerifyResult: ...

    @abstractmethod
    def settle(self, payment: dict, req: PaymentRequirements) -> dict: ...


class LocalFacilitator(Facilitator):
    def __init__(self, clock=time.time) -> None:
        self.balances: dict[str, int] = {}
        self.used: set[tuple[str, str]] = set()
        self.clock = clock
        self._lock = threading.Lock()
        self._tx = 0

    def fund(self, address: str, units: int) -> None:
        with self._lock:
            self.balances[address.lower()] = self.balances.get(address.lower(), 0) + units

    def verify(self, payment: dict, req: PaymentRequirements) -> VerifyResult:
        try:
            if payment.get("x402Version") != X402_VERSION:
                return VerifyResult(False, "unsupported x402 version")
            if payment.get("scheme") != req.scheme or payment.get("network") != req.network:
                return VerifyResult(False, "scheme/network mismatch")
            auth = payment["payload"]["authorization"]
            payer = auth["from"]
            if auth["to"].lower() != req.payTo.lower():
                return VerifyResult(False, "payTo mismatch", payer)
            if int(auth["value"]) != int(req.maxAmountRequired):
                return VerifyResult(False, "amount mismatch", payer)
            now = self.clock()
            if not int(auth["validAfter"]) <= now < int(auth["validBefore"]):
                return VerifyResult(False, "authorization outside validity window", payer)
            if recover_signer(req, payment).lower() != payer.lower():
                return VerifyResult(False, "invalid signature", payer)
            if (payer.lower(), auth["nonce"]) in self.used:
                return VerifyResult(False, "nonce already used", payer)
            if self.balances.get(payer.lower(), 0) < int(auth["value"]):
                return VerifyResult(False, "insufficient_funds", payer)
            return VerifyResult(True, payer=payer)
        except (KeyError, ValueError, TypeError) as exc:
            return VerifyResult(False, f"malformed payment: {exc}")

    def settle(self, payment: dict, req: PaymentRequirements) -> dict:
        with self._lock:
            v = self.verify(payment, req)
            if not v.valid:
                return {"success": False, "errorReason": v.reason, "payer": v.payer}
            auth = payment["payload"]["authorization"]
            value = int(auth["value"])
            self.used.add((v.payer.lower(), auth["nonce"]))
            self.balances[v.payer.lower()] -= value
            self.balances[req.payTo.lower()] = self.balances.get(req.payTo.lower(), 0) + value
            self._tx += 1
            return {"success": True, "transaction": f"0x{self._tx:064x}", "network": req.network, "payer": v.payer}
