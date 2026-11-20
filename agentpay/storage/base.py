"""Backend-neutral document + ledger store.

All SQL in the codebase goes through `Store.execute` with `?` placeholders and
the small set of dialect hooks below, so SQLite (dev/tests) and Postgres
(production) share one implementation of every query.
"""

from __future__ import annotations

import json
import threading
from abc import ABC, abstractmethod
from contextlib import contextmanager
from typing import Any, Iterator, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class Store(ABC):
    dialect: str = "abstract"

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._depth = 0

    # -- backend hooks ---------------------------------------------------
    @abstractmethod
    def _raw_execute(self, sql: str, params: tuple) -> Any: ...

    @abstractmethod
    def _begin(self) -> None: ...

    @abstractmethod
    def _commit(self) -> None: ...

    @abstractmethod
    def _rollback(self) -> None: ...

    @abstractmethod
    def _json_text(self, column: str, field: str) -> str:
        """SQL expression extracting a top-level JSON field as text."""

    @abstractmethod
    def _is_unique_violation(self, exc: Exception) -> bool: ...

    # -- transactions --------------------------------------------------
    @contextmanager
    def tx(self) -> Iterator["Store"]:
        """Re-entrant, serialized write transaction."""
        with self._lock:
            outer = self._depth == 0
            if outer:
                self._begin()
            self._depth += 1
            try:
                yield self
            except BaseException:
                self._depth -= 1
                if outer:
                    self._rollback()
                raise
            else:
                self._depth -= 1
                if outer:
                    self._commit()

    def execute(self, sql: str, params: tuple = ()) -> Any:
        with self._lock:
            return self._raw_execute(sql, params)

    def executemany(self, sql: str, rows: list[tuple]) -> None:
        with self.tx():
            for r in rows:
                self._raw_execute(sql, r)

    # -- documents -------------------------------------------------------
    def put(self, kind: str, model: BaseModel, *, k1: str | None = None) -> None:
        self.execute(
            "INSERT INTO docs(kind,id,body,k1,created_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(kind,id) DO UPDATE SET body=excluded.body, k1=excluded.k1",
            (kind, model.id, model.model_dump_json(), k1, getattr(model, "created_at", 0)),
        )

    def get(self, kind: str, id_: str, cls: type[T]) -> T | None:
        row = self.execute("SELECT body FROM docs WHERE kind=? AND id=?", (kind, id_)).fetchone()
        return cls.model_validate_json(row[0]) if row else None

    def list(self, kind: str, cls: type[T], *, k1: str | None = None, limit: int = 100) -> list[T]:
        if k1 is None:
            rows = self.execute("SELECT body FROM docs WHERE kind=? ORDER BY created_at DESC, id DESC LIMIT ?",
                                (kind, limit)).fetchall()
        else:
            rows = self.execute("SELECT body FROM docs WHERE kind=? AND k1=? ORDER BY created_at DESC, id DESC LIMIT ?",
                                (kind, k1, limit)).fetchall()
        return [cls.model_validate_json(r[0]) for r in rows]

    def find_one(self, kind: str, k1: str, cls: type[T]) -> T | None:
        row = self.execute("SELECT body FROM docs WHERE kind=? AND k1=? LIMIT 1", (kind, k1)).fetchone()
        return cls.model_validate_json(row[0]) if row else None

    def find_by_field(self, kind: str, field: str, value: str, cls: type[T]) -> T | None:
        if not field.isidentifier():
            raise ValueError("invalid field name")
        row = self.execute(f"SELECT body FROM docs WHERE kind=? AND {self._json_text('body', field)}=? LIMIT 1",
                           (kind, value)).fetchone()
        return cls.model_validate_json(row[0]) if row else None

    # -- nonces ------------------------------------------------------------
    def claim_nonce(self, scope: str, nonce: str, expires_at: int, now: int) -> bool:
        try:
            with self.tx():
                self._raw_execute("DELETE FROM nonces WHERE expires_at < ?", (now,))
                self._raw_execute("INSERT INTO nonces(scope,nonce,expires_at) VALUES(?,?,?)", (scope, nonce, expires_at))
            return True
        except Exception as exc:  # noqa: BLE001
            if self._is_unique_violation(exc):
                return False
            raise

    @staticmethod
    def dumps(obj: Any) -> str:
        return json.dumps(obj, separators=(",", ":"), default=str)

    def close(self) -> None:  # pragma: no cover
        pass


SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
    kind TEXT NOT NULL, id TEXT NOT NULL, body TEXT NOT NULL, k1 TEXT, created_at BIGINT,
    PRIMARY KEY (kind, id));
CREATE INDEX IF NOT EXISTS docs_k1 ON docs(kind, k1);
CREATE TABLE IF NOT EXISTS idempotency (
    scope TEXT NOT NULL, key TEXT NOT NULL, request_hash TEXT NOT NULL, status INTEGER,
    response TEXT, created_at BIGINT NOT NULL, PRIMARY KEY (scope, key));
CREATE TABLE IF NOT EXISTS nonces (
    scope TEXT NOT NULL, nonce TEXT NOT NULL, expires_at BIGINT NOT NULL, PRIMARY KEY (scope, nonce));
CREATE TABLE IF NOT EXISTS ledger (
    seq {serial}, txn_id TEXT NOT NULL, account TEXT NOT NULL, currency TEXT NOT NULL,
    debit BIGINT NOT NULL DEFAULT 0, credit BIGINT NOT NULL DEFAULT 0, ref TEXT, memo TEXT,
    created_at BIGINT NOT NULL);
CREATE INDEX IF NOT EXISTS ledger_account ON ledger(account, currency);
CREATE INDEX IF NOT EXISTS ledger_ref ON ledger(ref);
"""
