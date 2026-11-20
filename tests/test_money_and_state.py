import pytest

from agentpay.core.errors import InvalidStateTransition
from agentpay.core.models import IntentStatus as S, PaymentIntent
from agentpay.core.money import Money
from agentpay.core.state_machine import transition


@pytest.mark.parametrize("val,cur,minor", [("19.99", "USD", 1999), ("0.005", "USD", 1), ("100", "JPY", 100),
                                           ("1.2345", "KWD", 1235), (7, "EUR", 700)])
def test_from_major(val, cur, minor):
    assert Money.from_major(val, cur).amount == minor


@pytest.mark.parametrize("bad", ["abc", "NaN", "Infinity"])
def test_from_major_rejects_garbage(bad):
    with pytest.raises(ValueError):
        Money.from_major(bad, "USD")


def test_currency_validation_and_mismatch():
    with pytest.raises(ValueError):
        Money(100, "XXX")
    with pytest.raises(ValueError):
        Money(1, "USD") + Money(1, "EUR")
    with pytest.raises(TypeError):
        Money(1.5, "USD")  # type: ignore[arg-type]


def _pi():
    return PaymentIntent(agent_id="a", principal_id="p", mandate_id="m", amount=1, currency="USD", merchant_id="x")


def test_happy_path_transitions():
    pi = _pi()
    for s in (S.PROCESSING, S.REQUIRES_CAPTURE, S.SUCCEEDED, S.PARTIALLY_REFUNDED, S.REFUNDED):
        transition(pi, s)
    assert pi.status is S.REFUNDED


@pytest.mark.parametrize("path", [[S.SUCCEEDED], [S.PROCESSING, S.SUCCEEDED, S.CANCELED],
                                  [S.CANCELED, S.PROCESSING], [S.PROCESSING, S.FAILED, S.SUCCEEDED]])
def test_illegal_transitions(path):
    pi = _pi()
    with pytest.raises(InvalidStateTransition):
        for s in path:
            transition(pi, s)
