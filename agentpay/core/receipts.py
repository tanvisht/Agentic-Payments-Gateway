"""Verifiable receipts.

A receipt binds together what the principal authorized (mandate hash), what
the agent asked for (intent), what the processor did (provider ref, captured
and refunded amounts) and how it was booked (ledger journal), signed by the
gateway's Ed25519 key. Anyone holding the gateway public key can verify it
offline, e.g. an expense system or the principal's own audit tool.
"""

from __future__ import annotations

import io
from typing import Any

from agentpay.core import crypto
from agentpay.core.ids import now
from agentpay.core.models import Mandate, PaymentIntent
from agentpay.core.money import Money


def build(pi: PaymentIntent, mandate: Mandate, ledger_entries: list[dict], key: crypto.KeyPair) -> dict[str, Any]:
    body = {
        "type": "agentpay.receipt.v1",
        "issued_at": now(),
        "payment_intent": {"id": pi.id, "status": pi.status.value, "amount": pi.amount, "currency": pi.currency,
                           "amount_captured": pi.amount_captured, "amount_refunded": pi.amount_refunded,
                           "merchant_id": pi.merchant_id, "category": pi.category, "description": pi.description,
                           "provider": pi.provider, "provider_ref": pi.provider_ref, "created_at": pi.created_at},
        "agent_id": pi.agent_id,
        "principal_id": pi.principal_id,
        "mandate": {"id": mandate.id, "sha256": crypto.sha256_hex(crypto.canonical_json(
            {"scope": mandate.scope.model_dump(), "nonce": mandate.nonce, "signature": mandate.signature}))},
        "risk": pi.risk,
        "ledger": [{k: e[k] for k in ("txn_id", "account", "debit", "credit", "memo")} for e in ledger_entries],
    }
    return {**body, "signature": key.sign(body), "gateway_key": key.public_b64}


def verify(receipt: dict, gateway_public_key: str) -> bool:
    body = {k: v for k, v in receipt.items() if k not in ("signature", "gateway_key")}
    return crypto.verify(gateway_public_key, body, receipt.get("signature", ""))


def to_pdf(receipt: dict) -> bytes:
    from fpdf import FPDF  # optional dependency: pip install agentpay[receipts]

    pi = receipt["payment_intent"]
    money = lambda a: str(Money(a, pi["currency"]))  # noqa: E731
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 20)
    pdf.cell(0, 12, "AgentPay Receipt", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=10)
    rows = [("Payment", pi["id"]), ("Status", pi["status"]), ("Merchant", pi["merchant_id"]),
            ("Description", pi["description"] or "-"), ("Amount", money(pi["amount"])),
            ("Captured", money(pi["amount_captured"])), ("Refunded", money(pi["amount_refunded"])),
            ("Processor", f"{pi['provider']} {pi['provider_ref'] or ''}"), ("Agent", receipt["agent_id"]),
            ("Principal", receipt["principal_id"]), ("Mandate", f"{receipt['mandate']['id']}"),
            ("Mandate SHA-256", receipt["mandate"]["sha256"][:32] + "...")]
    for k, v in rows:
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(45, 7, k)
        pdf.set_font("Helvetica", size=10)
        pdf.cell(0, 7, str(v).encode("latin-1", "replace").decode("latin-1"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 8, "Ledger", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Courier", size=8)
    for e in receipt["ledger"]:
        pdf.cell(0, 5, f"{e['memo']:<10}{e['account']:<44}Dr {e['debit']:>9}  Cr {e['credit']:>9}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    pdf.set_font("Courier", size=6)
    pdf.multi_cell(0, 3, f"gateway key: {receipt['gateway_key']}\nsignature:   {receipt['signature']}")
    out = io.BytesIO()
    pdf.output(out)
    return out.getvalue()
