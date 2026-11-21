"""x402 wire format (scheme "exact", EIP-3009 transferWithAuthorization).

Server → 402 with PaymentRequirements; client signs an EIP-712
TransferWithAuthorization and retries with `X-PAYMENT: base64(json)`; server
asks a facilitator to verify + settle, then returns `X-PAYMENT-RESPONSE`.
"""

from __future__ import annotations

import base64
import json
import secrets
import time
from typing import Any

from pydantic import BaseModel, Field

X402_VERSION = 1

NETWORKS = {"base-sepolia": 84532, "base": 8453, "local": 1337}


class PaymentRequirements(BaseModel):
    scheme: str = "exact"
    network: str = "base-sepolia"
    maxAmountRequired: str            # token base units, decimal string
    resource: str
    description: str = ""
    mimeType: str = "application/json"
    payTo: str
    maxTimeoutSeconds: int = 120
    asset: str                        # token contract address
    extra: dict[str, Any] = Field(default_factory=lambda: {"name": "USD Coin", "version": "2"})


def typed_data(req: PaymentRequirements, auth: dict[str, Any]) -> dict[str, Any]:
    return {
        "types": {
            "EIP712Domain": [{"name": "name", "type": "string"}, {"name": "version", "type": "string"},
                             {"name": "chainId", "type": "uint256"}, {"name": "verifyingContract", "type": "address"}],
            "TransferWithAuthorization": [
                {"name": "from", "type": "address"}, {"name": "to", "type": "address"},
                {"name": "value", "type": "uint256"}, {"name": "validAfter", "type": "uint256"},
                {"name": "validBefore", "type": "uint256"}, {"name": "nonce", "type": "bytes32"}],
        },
        "primaryType": "TransferWithAuthorization",
        "domain": {"name": req.extra.get("name", "USD Coin"), "version": req.extra.get("version", "2"),
                   "chainId": NETWORKS[req.network], "verifyingContract": req.asset},
        "message": {"from": auth["from"], "to": auth["to"], "value": int(auth["value"]),
                    "validAfter": int(auth["validAfter"]), "validBefore": int(auth["validBefore"]),
                    "nonce": bytes.fromhex(auth["nonce"][2:])},
    }


def sign_payment(private_key: str, req: PaymentRequirements, *, value: int | None = None, now: int | None = None) -> dict:
    """Client side: produce an X-PAYMENT payload (dict) for the given requirements."""
    from eth_account import Account
    from eth_account.messages import encode_typed_data

    acct = Account.from_key(private_key)
    t = int(time.time()) if now is None else now
    auth = {"from": acct.address, "to": req.payTo, "value": str(value if value is not None else int(req.maxAmountRequired)),
            "validAfter": str(t - 5), "validBefore": str(t + req.maxTimeoutSeconds), "nonce": "0x" + secrets.token_hex(32)}
    signed = acct.sign_message(encode_typed_data(full_message=typed_data(req, auth)))
    return {"x402Version": X402_VERSION, "scheme": req.scheme, "network": req.network,
            "payload": {"signature": "0x" + signed.signature.hex().removeprefix("0x"), "authorization": auth}}


def recover_signer(req: PaymentRequirements, payment: dict) -> str:
    from eth_account import Account
    from eth_account.messages import encode_typed_data

    p = payment["payload"]
    return Account.recover_message(encode_typed_data(full_message=typed_data(req, p["authorization"])),
                                   signature=p["signature"])


def encode_header(payment: dict) -> str:
    return base64.b64encode(json.dumps(payment, separators=(",", ":")).encode()).decode()


def decode_header(value: str) -> dict:
    try:
        return json.loads(base64.b64decode(value, validate=True))
    except Exception as exc:  # noqa: BLE001
        raise ValueError("malformed X-PAYMENT header") from exc
