"""Property-based tests: random operation sequences must never break money invariants.

Invariants checked after every step:
  I1  ledger trial balance is zero per currency
  I2  mandate.spent + mandate.reserved <= max_total_amount, both >= 0
  I3  mandate.spent == Σ(captured − refunded) over its intents
  I4  mandate.reserved == Σ amount of intents still holding a reservation
  I5  merchant receivable == Σ(captured − refunded) to that merchant
  I6  every intent: 0 <= refunded <= captured <= amount
"""

import secrets

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from agentpay.agent.client import GatewayHTTPError
from agentpay.agent.demo import bootstrap, in_process_client
from agentpay.api.app import create_app
from agentpay.config import Settings
from agentpay.core.models import IntentStatus as S
from agentpay.core.models import PaymentIntent
from agentpay.gateway import Gateway
from agentpay.providers.mock import MockProvider
from agentpay.risk.engine import RiskConfig, RiskEngine

HOLDING = {S.REQUIRES_CAPTURE, S.REQUIRES_ACTION, S.PROCESSING}

op = st.one_of(
    st.tuples(st.just("pay"), st.integers(1, 30000), st.sampled_from(["auto", "manual"]),
              st.sampled_from(["pm_card_visa", "pm_card_visa", "pm_card_chargeDeclined"])),
    st.tuples(st.just("capture"), st.integers(0, 50), st.floats(0.01, 1.0)),
    st.tuples(st.just("cancel"), st.integers(0, 50)),
    st.tuples(st.just("refund"), st.integers(0, 50), st.floats(0.01, 1.0)),
)


def check(gw, mandate_id):
    m = gw.mandates.get(mandate_id)
    intents = [p for p in gw.store.list("payment_intent", PaymentIntent, limit=10000) if p.mandate_id == mandate_id]
    assert all(v == 0 for v in gw.ledger.trial_balance().values())                       # I1
    assert 0 <= m.spent_amount and 0 <= m.reserved_amount                               # I2
    assert m.spent_amount + m.reserved_amount <= m.scope.max_total_amount
    net = sum(p.amount_captured - p.amount_refunded for p in intents)
    assert m.spent_amount == net                                                         # I3
    assert m.reserved_amount == sum(p.amount for p in intents if p.status in HOLDING)    # I4
    assert gw.ledger.balance("merchant:nike.com:receivable", "USD") == net               # I5
    for p in intents:                                                                    # I6
        assert 0 <= p.amount_refunded <= p.amount_captured <= p.amount


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(ops=st.lists(op, min_size=1, max_size=25))
def test_money_invariants_hold_under_random_operations(ops):
    gw = Gateway.build(Settings(admin_key="x", async_webhooks=False, rate_burst=10**6, rate_per_s=10**6),
                       providers=[MockProvider("mock")],
                       risk=RiskEngine(RiskConfig(velocity_max=10**6, duplicate_window_s=0)))
    app = create_app(gw)
    _, _, _, akp, key, mandate = bootstrap(gw, per_txn=30000, total=60000)
    c = in_process_client(app, key, akp)
    ids: list[str] = []
    for o in ops:
        try:
            if o[0] == "pay":
                pi = c.create_payment(idempotency_key=secrets.token_hex(6), mandate_id=mandate.id, amount=o[1],
                                      currency="USD", merchant_id="nike.com", category="running",
                                      capture_method="automatic" if o[2] == "auto" else "manual", payment_method=o[3])
                ids.append(pi["id"])
            elif ids:
                pid = ids[o[1] % len(ids)]
                cur = gw.payments.get(pid)
                if o[0] == "capture":
                    c.request("POST", f"/v1/payment_intents/{pid}/capture", {"amount": max(1, int(cur.amount * o[2]))})
                elif o[0] == "cancel":
                    c.request("POST", f"/v1/payment_intents/{pid}/cancel")
                else:
                    left = cur.amount_captured - cur.amount_refunded
                    c.refund(pid, max(1, int(left * o[2])) if left else 1)
        except GatewayHTTPError as e:
            assert e.status in (402, 403, 409, 422), e.body  # only business-rule rejections, never 5xx
        check(gw, mandate.id)


@settings(max_examples=40, deadline=None)
@given(amounts=st.lists(st.integers(-10**12, 10**12), min_size=2, max_size=6))
def test_ledger_rejects_any_unbalanced_entry(amounts):
    from agentpay.core.errors import ValidationFailed
    from agentpay.ledger.ledger import Ledger, Posting
    from agentpay.storage import SqliteStore

    led = Ledger(SqliteStore())
    postings = [Posting(f"a{i}", debit=a) if a >= 0 else Posting(f"a{i}", credit=-a) for i, a in enumerate(amounts)]
    balanced = sum(p.debit for p in postings) == sum(p.credit for p in postings)
    if balanced:
        led.post(postings, currency="USD", ref="r")
    else:
        with pytest.raises(ValidationFailed):
            led.post(postings, currency="USD", ref="r")
    assert led.trial_balance().get("USD", 0) == 0


@settings(max_examples=80, deadline=None)
@given(value=st.decimals(min_value=0, max_value=10**9, places=4, allow_nan=False),
       cur=st.sampled_from(["USD", "JPY", "KWD"]))
def test_money_round_trip(value, cur):
    from agentpay.core.money import Money, exponent
    m = Money.from_major(value, cur)
    assert Money.from_major(m.to_major(), cur) == m
    assert abs(m.to_major() - value) <= __import__("decimal").Decimal(1).scaleb(-exponent(cur)) / 2
