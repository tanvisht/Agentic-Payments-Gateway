from __future__ import annotations

import sqlite3

from agentpay.storage.base import SCHEMA, Store


class SqliteStore(Store):
    dialect = "sqlite"

    def __init__(self, path: str = ":memory:") -> None:
        super().__init__()
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        if path != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(SCHEMA.format(serial="INTEGER PRIMARY KEY AUTOINCREMENT"))

    def _raw_execute(self, sql, params):
        return self._conn.execute(sql, params)

    def _begin(self):
        self._conn.execute("BEGIN IMMEDIATE")

    def _commit(self):
        self._conn.execute("COMMIT")

    def _rollback(self):
        self._conn.execute("ROLLBACK")

    def _json_text(self, column, field):
        return f"json_extract({column}, '$.{field}')"

    def _is_unique_violation(self, exc):
        return isinstance(exc, sqlite3.IntegrityError)

    def close(self):
        self._conn.close()
