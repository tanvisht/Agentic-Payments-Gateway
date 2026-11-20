"""Outbound webhook signatures: `AgentPay-Signature: t=<unix>,v1=<hex hmac>`.

HMAC-SHA256 over `"{t}.{raw_body}"`. Receivers must reject timestamps older
than the tolerance to prevent replay.
"""

from __future__ import annotations

import time

from agentpay.core.crypto import constant_eq, hmac_sha256

HEADER = "AgentPay-Signature"


def sign(secret: str, body: bytes, ts: int | None = None) -> str:
    t = int(time.time()) if ts is None else ts
    return f"t={t},v1={hmac_sha256(secret, f'{t}.'.encode() + body)}"


def verify(secret: str, body: bytes, header: str, *, tolerance_s: int = 300, now: int | None = None) -> bool:
    try:
        parts = dict(kv.split("=", 1) for kv in header.split(","))
        t = int(parts["t"])
        sigs = [v for k, v in (kv.split("=", 1) for kv in header.split(",")) if k == "v1"]
    except (ValueError, KeyError):
        return False
    current = int(time.time()) if now is None else now
    if abs(current - t) > tolerance_s:
        return False
    expected = hmac_sha256(secret, f"{t}.".encode() + body)
    return any(constant_eq(expected, s) for s in sigs)
