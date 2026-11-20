"""Explicit payment-intent lifecycle. Every status change goes through `transition`."""

from __future__ import annotations

from agentpay.core.errors import InvalidStateTransition
from agentpay.core.ids import now
from agentpay.core.models import IntentStatus as S
from agentpay.core.models import PaymentIntent

TRANSITIONS: dict[S, frozenset[S]] = {
    S.REQUIRES_CONFIRMATION: frozenset({S.PROCESSING, S.CANCELED}),
    S.PROCESSING: frozenset({S.REQUIRES_ACTION, S.REQUIRES_CAPTURE, S.SUCCEEDED, S.FAILED}),
    S.REQUIRES_ACTION: frozenset({S.PROCESSING, S.REQUIRES_CAPTURE, S.SUCCEEDED, S.CANCELED, S.FAILED}),
    S.REQUIRES_CAPTURE: frozenset({S.SUCCEEDED, S.CANCELED}),
    S.SUCCEEDED: frozenset({S.PARTIALLY_REFUNDED, S.REFUNDED}),
    S.PARTIALLY_REFUNDED: frozenset({S.PARTIALLY_REFUNDED, S.REFUNDED}),
    S.FAILED: frozenset({S.PROCESSING}),  # retry with a new payment method
    S.REFUNDED: frozenset(),
    S.CANCELED: frozenset(),
}

TERMINAL = frozenset({S.REFUNDED, S.CANCELED})


def can_transition(src: S, dst: S) -> bool:
    return dst in TRANSITIONS[src]


def transition(intent: PaymentIntent, dst: S) -> PaymentIntent:
    if not can_transition(intent.status, dst):
        raise InvalidStateTransition(
            f"cannot move payment intent from {intent.status.value} to {dst.value}",
            details={"from": intent.status.value, "to": dst.value},
        )
    intent.status = dst
    intent.updated_at = now()
    return intent
