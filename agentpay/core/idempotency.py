"""Idempotency keys: same key + same request → same response, forever (24h TTL).

Same key + *different* request is a client bug and is rejected. A key whose
first request is still executing is rejected with 409 so a retrying agent can
never trigger two concurrent charges.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from agentpay.core.crypto import canonical_json, sha256_hex
from agentpay.core.errors import IdempotencyConflict
from agentpay.core.ids import now
from agentpay.storage import Store

TTL_S = 24 * 3600


@dataclass
class Cached:
    status: int
    body: dict


class Idempotency:
    def __init__(self, store: Store) -> None:
        self.store = store

    @staticmethod
    def fingerprint(method: str, path: str, body: dict | None) -> str:
        return sha256_hex(canonical_json({"m": method, "p": path, "b": body or {}}))

    def begin(self, scope: str, key: str, fingerprint: str) -> Cached | None:
        """Returns a cached response to replay, or None if the caller should execute."""
        if not key or len(key) > 255:
            raise IdempotencyConflict("Idempotency-Key must be 1-255 characters")
        with self.store.tx() as c:
            c.execute("DELETE FROM idempotency WHERE created_at < ?", (now() - TTL_S,))
            row = c.execute("SELECT request_hash,status,response FROM idempotency WHERE scope=? AND key=?",
                            (scope, key)).fetchone()
            if row is None:
                c.execute("INSERT INTO idempotency(scope,key,request_hash,created_at) VALUES(?,?,?,?)",
                          (scope, key, fingerprint, now()))
                return None
        req_hash, status, response = row
        if req_hash != fingerprint:
            raise IdempotencyConflict("Idempotency-Key reused with a different request body")
        if status is None:
            raise IdempotencyConflict("a request with this Idempotency-Key is still in progress")
        return Cached(status, json.loads(response))

    def complete(self, scope: str, key: str, status: int, body: dict) -> None:
        self.store.execute("UPDATE idempotency SET status=?, response=? WHERE scope=? AND key=?",
                           (status, json.dumps(body, default=str), scope, key))

    def abandon(self, scope: str, key: str) -> None:
        """Unexpected crash before a response existed: allow a clean retry."""
        self.store.execute("DELETE FROM idempotency WHERE scope=? AND key=? AND status IS NULL", (scope, key))
