"""Ed25519 signing over canonical JSON, plus HMAC helpers.

Canonical form: UTF-8 JSON, sorted keys, no whitespace. Both mandate signatures
(principal → agent delegation) and request signatures (agent → gateway) use it,
so any SDK in any language can reproduce the exact bytes.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat


def canonical_json(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class KeyPair:
    def __init__(self, private: Ed25519PrivateKey):
        self._sk = private

    @classmethod
    def generate(cls) -> "KeyPair":
        return cls(Ed25519PrivateKey.generate())

    @classmethod
    def from_private_b64(cls, s: str) -> "KeyPair":
        return cls(Ed25519PrivateKey.from_private_bytes(b64d(s)))

    @property
    def private_b64(self) -> str:
        return b64e(self._sk.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()))

    @property
    def public_b64(self) -> str:
        return b64e(self._sk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw))

    def sign(self, payload: Any) -> str:
        data = payload if isinstance(payload, bytes) else canonical_json(payload)
        return b64e(self._sk.sign(data))


def verify(public_b64: str, payload: Any, signature_b64: str) -> bool:
    try:
        pk = Ed25519PublicKey.from_public_bytes(b64d(public_b64))
        data = payload if isinstance(payload, bytes) else canonical_json(payload)
        pk.verify(b64d(signature_b64), data)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


def sha256_hex(data: bytes | str) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def hmac_sha256(secret: str, msg: bytes) -> str:
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def constant_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def new_api_key(prefix: str = "ak") -> str:
    return f"{prefix}_{secrets.token_urlsafe(32)}"
