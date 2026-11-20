"""Postgres backend (psycopg 3). Install with `pip install agentpay[postgres]`.

One connection guarded by the store lock gives the same serializable write
semantics as the SQLite backend. For horizontal scale, the mandate
reservation path should move to `SELECT ... FOR UPDATE` row locks with a
connection pool; the Store interface already isolates that change.
"""

from __future__ import annotations

from agentpay.storage.base import SCHEMA, Store


class _Cursor:
    def __init__(self, cur):
        self._cur = cur

    def fetchone(self):
        return self._cur.fetchone() if self._cur.description else None

    def fetchall(self):
        return self._cur.fetchall() if self._cur.description else []


class PostgresStore(Store):
    dialect = "postgres"

    def __init__(self, dsn: str) -> None:
        super().__init__()
        import psycopg

        self._psycopg = psycopg
        self._conn = psycopg.connect(dsn, autocommit=True)
        for stmt in SCHEMA.format(serial="BIGSERIAL PRIMARY KEY").split(";"):
            if stmt.strip():
                self._conn.execute(stmt)

    def _raw_execute(self, sql, params):
        return _Cursor(self._conn.execute(sql.replace("?", "%s"), params))

    def _begin(self):
        self._conn.execute("BEGIN ISOLATION LEVEL SERIALIZABLE")

    def _commit(self):
        self._conn.execute("COMMIT")

    def _rollback(self):
        self._conn.execute("ROLLBACK")

    def _json_text(self, column, field):
        return f"(({column})::jsonb ->> '{field}')"

    def _is_unique_violation(self, exc):
        return isinstance(exc, self._psycopg.errors.UniqueViolation)

    def claim_nonce(self, scope, nonce, expires_at, now):
        # A failed INSERT aborts a Postgres transaction; use ON CONFLICT instead.
        with self.tx():
            self._raw_execute("DELETE FROM nonces WHERE expires_at < ?", (now,))
            cur = self._conn.execute(
                "INSERT INTO nonces(scope,nonce,expires_at) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING RETURNING nonce",
                (scope, nonce, expires_at))
            return cur.fetchone() is not None

    def close(self):
        self._conn.close()
