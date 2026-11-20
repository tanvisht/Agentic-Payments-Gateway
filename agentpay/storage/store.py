"""Document store on SQLite.

Each collection is a table of (id, json, indexed columns). SQLite gives us
atomic multi-row transactions — required so a mandate reservation, an intent
update and ledger postings commit together or not at all. `:memory:` is used
in tests; a file path in dev; swap for Postgres by reimplementing this class.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Iterator, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
    kind TEXT NOT NULL,
    id   TEXT NOT NULL,
    body TEXT NOT NULL,
    k1   TEXT,           -- secondary index (e.g. agent_id)
    created_at INTEGER,
    PRIMARY KEY (kind, id)
);
CREATE INDEX IF NOT EXISTS docs_k1 ON docs(kind, k1);
CREATE TABLE IF NOT EXISTS idempotency (
    scope TEXT NOT NULL,
    key   TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    status INTEGER,
    response TEXT,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (scope, key)
);
CREATE TABLE IF NOT EXISTS nonces (
    scope TEXT NOT NULL,
    nonce TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    PRIMARY KEY (scope, nonce)
);
CREATE TABLE IF NOT EXISTS ledger (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    txn_id TEXT NOT NULL,
    account TEXT NOT NULL,
    currency TEXT NOT NULL,
    debit INTEGER NOT NULL DEFAULT 0,
    credit INTEGER NOT NULL DEFAULT 0,
    ref TEXT,
    memo TEXT,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ledger_account ON ledger(account, currency);
"""


class Store:
    def __init__(self, path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL") if path != ":memory:" else None
        self._conn.executescript(_SCHEMA)
        self._lock = threading.RLock()
        self._depth = 0

    # -- transactions -------------------------------------------------
    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Re-entrant, serialized write transaction."""
        with self._lock:
            outer = self._depth == 0
            if outer:
                self._conn.execute("BEGIN IMMEDIATE")
            self._depth += 1
            try:
                yield self._conn
            except BaseException:
                self._depth -= 1
                if outer:
                    self._conn.execute("ROLLBACK")
                raise
            else:
                self._depth -= 1
                if outer:
                    self._conn.execute("COMMIT")

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    # -- documents ----------------------------------------------------
    def put(self, kind: str, model: BaseModel, *, k1: str | None = None) -> None:
        body = model.model_dump_json()
        with self.tx() as c:
            c.execute(
                "INSERT INTO docs(kind,id,body,k1,created_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(kind,id) DO UPDATE SET body=excluded.body, k1=excluded.k1",
                (kind, model.id, body, k1, getattr(model, "created_at", 0)),
            )

    def get(self, kind: str, id_: str, cls: type[T]) -> T | None:
        row = self.execute("SELECT body FROM docs WHERE kind=? AND id=?", (kind, id_)).fetchone()
        return cls.model_validate_json(row[0]) if row else None

    def list(self, kind: str, cls: type[T], *, k1: str | None = None, limit: int = 100) -> list[T]:
        if k1 is None:
            rows = self.execute(
                "SELECT body FROM docs WHERE kind=? ORDER BY created_at DESC, id DESC LIMIT ?", (kind, limit)
            ).fetchall()
        else:
            rows = self.execute(
                "SELECT body FROM docs WHERE kind=? AND k1=? ORDER BY created_at DESC, id DESC LIMIT ?",
                (kind, k1, limit),
            ).fetchall()
        return [cls.model_validate_json(r[0]) for r in rows]

    def find_one(self, kind: str, k1: str, cls: type[T]) -> T | None:
        row = self.execute("SELECT body FROM docs WHERE kind=? AND k1=? LIMIT 1", (kind, k1)).fetchone()
        return cls.model_validate_json(row[0]) if row else None

    # -- nonces (replay protection) ----------------------------------
    def claim_nonce(self, scope: str, nonce: str, expires_at: int, now: int) -> bool:
        with self.tx() as c:
            c.execute("DELETE FROM nonces WHERE expires_at < ?", (now,))
            try:
                c.execute("INSERT INTO nonces(scope,nonce,expires_at) VALUES(?,?,?)", (scope, nonce, expires_at))
                return True
            except sqlite3.IntegrityError:
                return False

    @staticmethod
    def dumps(obj: Any) -> str:
        return json.dumps(obj, separators=(",", ":"), default=str)
