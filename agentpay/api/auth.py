"""Request authentication.

Admin/operator calls: `Authorization: Bearer <AGENTPAY_ADMIN_KEY>`.

Agent calls: `Authorization: Bearer ak_...` **plus** a detached Ed25519
signature, so a leaked API key alone cannot move money:

    X-AgentPay-Timestamp: <unix seconds>
    X-AgentPay-Nonce:     <random, single use>
    X-AgentPay-Signature: b64url(Ed25519(canonical_json({
        "method": "POST", "path": "/v1/payment_intents",
        "timestamp": <ts>, "nonce": <nonce>, "body_sha256": <hex>
    })))
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from agentpay.core import crypto
from agentpay.core.errors import AuthenticationError, ReplayError, SignatureError
from agentpay.core.ids import now
from agentpay.core.models import Agent
from agentpay.gateway import Gateway


@dataclass
class Caller:
    kind: str  # admin | agent
    agent: Agent | None = None

    @property
    def scope(self) -> str:
        return "admin" if self.kind == "admin" else f"agent:{self.agent.id}"  # type: ignore[union-attr]


def request_signing_payload(method: str, path: str, timestamp: int, nonce: str, body: bytes) -> dict:
    return {"method": method.upper(), "path": path, "timestamp": timestamp, "nonce": nonce,
            "body_sha256": crypto.sha256_hex(body)}


def _bearer(request: Request) -> str:
    h = request.headers.get("authorization", "")
    if not h.lower().startswith("bearer "):
        raise AuthenticationError("missing bearer token")
    return h[7:].strip()


async def authenticate(gw: Gateway, request: Request, *, require_signature: bool) -> Caller:
    token = _bearer(request)
    if gw.settings.admin_key and crypto.constant_eq(token, gw.settings.admin_key):
        return Caller("admin")
    agent = gw.agent_by_api_key(token) if token.startswith("ak_") else None
    if agent is None:
        raise AuthenticationError("invalid API key")
    if agent.status != "active":
        raise AuthenticationError("agent is suspended")
    if require_signature:
        await verify_agent_signature(gw, request, agent)
    return Caller("agent", agent)


async def verify_agent_signature(gw: Gateway, request: Request, agent: Agent) -> None:
    sig = request.headers.get("x-agentpay-signature")
    ts_raw = request.headers.get("x-agentpay-timestamp")
    nonce = request.headers.get("x-agentpay-nonce")
    if not (sig and ts_raw and nonce):
        raise SignatureError("missing X-AgentPay-Signature / -Timestamp / -Nonce headers")
    try:
        ts = int(ts_raw)
    except ValueError:
        raise SignatureError("invalid timestamp") from None
    tol = gw.settings.signature_tolerance_s
    if abs(now() - ts) > tol:
        raise SignatureError("request timestamp outside tolerance window")
    body = await request.body()
    payload = request_signing_payload(request.method, request.url.path, ts, nonce, body)
    if not crypto.verify(agent.public_key, payload, sig):
        raise SignatureError("request signature does not verify against agent key")
    if not gw.store.claim_nonce(f"req:{agent.id}", nonce, ts + tol, now()):
        raise ReplayError("nonce already used")
