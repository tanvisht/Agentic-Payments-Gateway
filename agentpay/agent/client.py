"""Agent-side SDK: signs every request with the agent's Ed25519 key."""

from __future__ import annotations

import json
import secrets
import time
from typing import Any

import httpx

from agentpay.api.auth import request_signing_payload
from agentpay.core.crypto import KeyPair


class GatewayClient:
    def __init__(self, base_url: str, api_key: str, keypair: KeyPair, *, transport: httpx.BaseTransport | None = None) -> None:
        self.api_key, self.kp = api_key, keypair
        self.http = httpx.Client(base_url=base_url, transport=transport, timeout=30.0)

    def _headers(self, method: str, path: str, body: bytes, idempotency_key: str | None) -> dict[str, str]:
        ts, nonce = int(time.time()), secrets.token_hex(16)
        h = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json",
             "X-AgentPay-Timestamp": str(ts), "X-AgentPay-Nonce": nonce,
             "X-AgentPay-Signature": self.kp.sign(request_signing_payload(method, path, ts, nonce, body))}
        if idempotency_key:
            h["Idempotency-Key"] = idempotency_key
        return h

    def request(self, method: str, path: str, body: dict | None = None, *, idempotency_key: str | None = None) -> dict[str, Any]:
        raw = json.dumps(body).encode() if body is not None else b""
        r = self.http.request(method, path, content=raw or None, headers=self._headers(method, path, raw, idempotency_key))
        data = r.json()
        if r.status_code >= 400:
            raise GatewayHTTPError(r.status_code, data)
        return data

    # -- convenience ---------------------------------------------------
    def create_payment(self, idempotency_key: str | None = None, **body: Any) -> dict:
        return self.request("POST", "/v1/payment_intents", body, idempotency_key=idempotency_key or secrets.token_hex(12))

    def get_payment(self, intent_id: str) -> dict:
        return self.request("GET", f"/v1/payment_intents/{intent_id}")

    def get_mandate(self, mandate_id: str) -> dict:
        return self.request("GET", f"/v1/mandates/{mandate_id}")

    def refund(self, intent_id: str, amount: int | None = None, reason: str = "requested_by_customer") -> dict:
        return self.request("POST", f"/v1/payment_intents/{intent_id}/refunds", {"amount": amount, "reason": reason})


class GatewayHTTPError(Exception):
    def __init__(self, status: int, body: dict) -> None:
        self.status, self.body = status, body
        err = body.get("error", {}) if isinstance(body, dict) else {}
        super().__init__(f"{status} {err.get('code')}: {err.get('message')}")
