"""Buyer-side ACP client: talks JSON-RPC to a merchant and verifies its signed offers."""

from __future__ import annotations

import itertools
from typing import Any

from agentpay.acp.service import MerchantService


class AcpError(Exception):
    def __init__(self, err: dict) -> None:
        self.error = err
        super().__init__(err.get("message"))


class AcpClient:
    def __init__(self, http, path: str = "/acp/rpc") -> None:
        self.http, self.path = http, path
        self._ids = itertools.count(1)
        self.merchant_key: str | None = None
        self.session_id: str | None = None

    def call(self, method: str, **params: Any) -> Any:
        r = self.http.post(self.path, json={"jsonrpc": "2.0", "id": next(self._ids), "method": method, "params": params})
        body = r.json()
        if "error" in body:
            raise AcpError(body["error"])
        return body["result"]

    def connect(self, buyer_id: str) -> str:
        self.merchant_key = self.call("initialize")["merchantKey"]
        self.session_id = self.call("session/new", buyerId=buyer_id)["sessionId"]
        return self.session_id

    def prompt(self, text: str) -> dict:
        res = self.call("session/prompt", sessionId=self.session_id, text=text)
        if res.get("type") == "offers":
            for o in res["offers"]:
                o["signature_valid"] = bool(self.merchant_key) and MerchantService.verify_offer(
                    {k: v for k, v in o.items() if k != "signature_valid"}, self.merchant_key)
        return res

    def confirm(self, offer_id: str, payment_intent_id: str) -> dict:
        return self.call("commerce/pay", sessionId=self.session_id, offerId=offer_id, paymentIntentId=payment_intent_id)
