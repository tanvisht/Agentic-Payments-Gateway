"""Append-only double-entry ledger.

Every money movement is a balanced journal transaction (sum debits == sum
credits per currency). Balances are derived, never stored, so the ledger is
the audit trail and reconciliation source against provider settlement reports.

Accounts
--------
principal:<id>:funding     principal's payment method (source of funds)
merchant:<id>:receivable   owed to the merchant by the processor
provider:<name>:held       authorized-but-not-captured funds held at processor
"""

from __future__ import annotations

from dataclasses import dataclass

from agentpay.core.errors import ValidationFailed
from agentpay.core.ids import new_id, now
from agentpay.storage import Store


@dataclass(frozen=True)
class Posting:
    account: str
    debit: int = 0
    credit: int = 0


class Ledger:
    def __init__(self, store: Store) -> None:
        self.store = store

    def post(self, postings: list[Posting], *, currency: str, ref: str, memo: str = "") -> str:
        if len(postings) < 2:
            raise ValidationFailed("a journal entry needs at least two postings")
        if any(p.debit < 0 or p.credit < 0 or (p.debit and p.credit) for p in postings):
            raise ValidationFailed("each posting is a single non-negative debit or credit")
        if sum(p.debit for p in postings) != sum(p.credit for p in postings):
            raise ValidationFailed("unbalanced journal entry")
        txn = new_id("jrnl")
        ts = now()
        with self.store.tx() as c:
            c.executemany(
                "INSERT INTO ledger(txn_id,account,currency,debit,credit,ref,memo,created_at) VALUES(?,?,?,?,?,?,?,?)",
                [(txn, p.account, currency, p.debit, p.credit, ref, memo, ts) for p in postings],
            )
        return txn

    def balance(self, account: str, currency: str) -> int:
        """Credit-normal balance (credits − debits)."""
        row = self.store.execute(
            "SELECT COALESCE(SUM(credit),0)-COALESCE(SUM(debit),0) FROM ledger WHERE account=? AND currency=?",
            (account, currency),
        ).fetchone()
        return int(row[0])

    def entries(self, ref: str) -> list[dict]:
        rows = self.store.execute(
            "SELECT txn_id,account,currency,debit,credit,memo,created_at FROM ledger WHERE ref=? ORDER BY seq", (ref,)
        ).fetchall()
        keys = ("txn_id", "account", "currency", "debit", "credit", "memo", "created_at")
        return [dict(zip(keys, r)) for r in rows]

    def trial_balance(self) -> dict[str, int]:
        """Per-currency sum of debits − credits. Must be all zeros."""
        rows = self.store.execute("SELECT currency, SUM(debit)-SUM(credit) FROM ledger GROUP BY currency").fetchall()
        return {cur: int(v) for cur, v in rows}

    # -- domain helpers ----------------------------------------------
    def record_authorization(self, *, intent_id: str, principal_id: str, provider: str, amount: int, currency: str) -> None:
        self.post([Posting(f"principal:{principal_id}:funding", debit=amount),
                   Posting(f"provider:{provider}:held", credit=amount)], currency=currency, ref=intent_id, memo="authorize")

    def record_capture(self, *, intent_id: str, principal_id: str, provider: str, merchant_id: str,
                       authorized: int, captured: int, currency: str, from_hold: bool) -> None:
        if from_hold:
            postings = [Posting(f"provider:{provider}:held", debit=authorized),
                        Posting(f"merchant:{merchant_id}:receivable", credit=captured)]
            if authorized > captured:
                postings.append(Posting(f"principal:{principal_id}:funding", credit=authorized - captured))
        else:
            postings = [Posting(f"principal:{principal_id}:funding", debit=captured),
                        Posting(f"merchant:{merchant_id}:receivable", credit=captured)]
        self.post(postings, currency=currency, ref=intent_id, memo="capture")

    def record_void(self, *, intent_id: str, principal_id: str, provider: str, amount: int, currency: str) -> None:
        self.post([Posting(f"provider:{provider}:held", debit=amount),
                   Posting(f"principal:{principal_id}:funding", credit=amount)], currency=currency, ref=intent_id, memo="void")

    def record_refund(self, *, intent_id: str, principal_id: str, merchant_id: str, amount: int, currency: str) -> None:
        self.post([Posting(f"merchant:{merchant_id}:receivable", debit=amount),
                   Posting(f"principal:{principal_id}:funding", credit=amount)], currency=currency, ref=intent_id, memo="refund")
